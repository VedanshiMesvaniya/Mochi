# Mochi v1.1 - Web Knowledge & Context Engine

Version: 1.1
Status: Partially implemented (opt-in, off by default) - see "Implementation status" below
Project: Mochi
Purpose: Fresh, context-aware, evidence-backed knowledge acquisition

---

## Implementation status (read this first)

This file is the original v1.1 design spec, kept in full below so future
work can be checked against it. The current codebase implements a first,
deliberately narrower slice of it, gated entirely behind
`settings.web_knowledge_enabled` (`MOCHI_WEB_KNOWLEDGE_ENABLED` in
`.env`, off by default) so it can never change behavior for anyone who
hasn't opted in. See `PROJECT_ARCHITECTURE.md` section 5i for the actual
pipeline diagram and code pointers (`app/knowledge/`).

**Implemented:**

- Source Manager (section 5/6) - a small fixed registry, currently one
  RSS feed and one subreddit (`app/knowledge/source_manager.py`)
- Scheduler (section 8) - per-source frequency policy
  (`app/knowledge/scheduler.py`)
- Incremental Fetching (section 9) - ETag/Last-Modified for RSS,
  content-hash for Reddit (`app/knowledge/fetcher.py`)
- Fetcher / Parser / Normalization (sections 10-12)
  (`app/knowledge/fetcher.py`, `app/knowledge/parser.py`)
- Deduplication (section 13) - exact URL and same-source content-hash
  matching (`app/knowledge/dedup.py`)
- Classification into Temporal Feed vs Persistent Knowledge (sections
  15-17) - source-level, with TTL assignment (`app/knowledge/classifier.py`)
- Reddit as a temporal source (section 18)
- Knowledge Store, Layer A only - raw documents with full provenance
  (section 19) (`app/knowledge/knowledge_store.py`)
- Freshness Engine and Freshness Categories (sections 21-22)
  (`app/knowledge/freshness.py`)
- Freshness Router (section 23), narrowed to a "does this look current"
  check rather than the full four-way query split (`app/knowledge/context_engine.py`)
- Answer Generation via a compact evidence package (section 30), passed
  into `app/ai/llm.ask`'s `web_context` parameter
- Provenance (section 31) - every stored document keeps its source key
  and URL

**Deferred** (not yet built - noted here so it isn't rediscovered as a
gap by accident):

- Knowledge Store Layer B (semantic/vector index, section 19) and Layer C
  (structured claims table, section 19) - retrieval currently uses a
  lightweight keyword-overlap relevance score instead, to keep this
  package free of an embeddings dependency
- Claim Extraction and Claim Verification / contradiction resolution
  (sections 25-27) - documents are stored and ranked as whole units, not
  decomposed into individually verified claims
- Near-duplicate similarity across differently-phrased retellings of the
  same story (section 13) - only exact URL/content-hash duplicates are
  caught today
- Sitemap-based and browser-automation acquisition (sections 6/7.3/7.5) -
  only the API-like RSS/JSON paths are implemented
- The full four-way Query Categories split (section 24) - "Live
  Information" and "Personal Context" routing already happen elsewhere in
  Mochi (conversation memory, reminders/tasks lookups) and were not
  duplicated here; this engine only adds the "Current Knowledge" path
- A user-editable source registry (section 5) - today's registry is a
  fixed, hardcoded list; making it configurable is a natural next step

None of the above are required for the opt-in feature to work safely -
they're documented as future expansion, matching this project's existing
pattern of scoping a feature down to a safe, testable first version
before building further (see `docs/ROADMAP.md`'s own V1.0 -> V1.2 -> V2.0
staged approach).

---

## 1. Overview

Mochi v1.1 introduces a Web Knowledge Engine that allows Mochi to acquire, maintain, retrieve, and verify information from selected external sources.

The goal is not to turn Mochi into a generic web scraper.

The goal is:

"Mochi continuously maintains a small, relevant, freshness-aware, and evidence-backed knowledge layer from selected external sources."

This allows Mochi to answer both:

- stable questions using local knowledge,
- and time-sensitive questions using fresh external information.

Examples:

"What is a transformer?"

-> Local knowledge is sufficient.

"What is the latest Python version?"

-> Fresh external information is required.

"What's trending on Reddit today?"

-> Live/near-live source retrieval is required.

"What did I tell you about my project yesterday?"

-> Personal context/memory is required.

---

## 2. Why v1.1 Needs a Web Knowledge Engine

Mochi's local knowledge becomes outdated.

Information changes continuously:

- software versions
- documentation
- news
- products
- APIs
- GitHub projects
- internet trends
- Reddit discussions
- technical articles
- public information

A normal static RAG system has a major problem:

```text
Old knowledge
     |
Embedding
     |
Retrieval
     |
Confident but outdated answer
```

Mochi v1.1 instead introduces freshness awareness:

```text
External Sources
       |
    Ingestion
       |
   Processing
       |
   Verification
       |
Knowledge Store
       |
Freshness-aware Retrieval
       |
Mochi Answer
```

---

## 3. Core Design Principle

Mochi should not trust information simply because it exists online.

Every piece of retrieved information should have context about:

Source, Published time, Retrieved time, Last verified time, Confidence,
Authority, Freshness, Evidence.

Therefore, Mochi should reason over evidence, not simply over scraped text.

Example:

Claim: Python X supports feature Y
Source: Official documentation
Retrieved: 2 hours ago
Authority: High
Confidence: 0.96
Freshness: Fresh

Compared with:

Claim: Python X supports feature Y
Source: Random blog
Retrieved: 14 months ago
Authority: Low/Medium
Confidence: 0.51
Freshness: Potentially stale

---

## 4. v1.1 Architecture

```text
                         External Web
                                |
                     APIs / RSS   Web Sources
                                |
                         Source Manager
                                |
                            Scheduler
                                |
                             Fetcher
                                |
                             Parser
                                |
                          Normalization
                                |
                          Deduplication
                                |
                       Content Extractor
                                |
                           Classifier
                                |
                Temporal Feed        Knowledge
                     |                   |
                Short TTL             Long TTL
                     |___________________|
                                |
                       Claim Verification
                                |
                          Knowledge Store
                                |
                        Search / Vector Index
                                |
                          Context Engine
                                |
                          Mochi Response
```

---

## 5. Source Manager

The Source Manager controls what Mochi is allowed and expected to collect.

Mochi should not crawl the entire internet.

Instead, it maintains a Source Registry.

Example:

```text
sources/
|-- reddit
|   |-- r/popular
|   |-- r/memes
|   |-- r/MemeEconomy
|   `-- r/popculture
|-- news
|-- documentation
|-- blogs
|-- github
`-- user_selected_sites
```

Each source has metadata.

Example:

```json
{
  "source": "reddit",
  "target": "r/memes",
  "type": "community",
  "update_frequency": "high",
  "importance": "medium",
  "enabled": true
}
```

---

## 6. Source Acquisition Priority

Mochi should prefer structured and officially supported sources.

The preferred acquisition order is:

1. Official API
2. RSS / Atom
3. Sitemap
4. HTML extraction
5. Browser automation

HTML scraping should be a fallback rather than the default.

Browser automation should only be used when simpler acquisition methods are unavailable or insufficient.

---

## 7. Source Types

v1.1 should support several source categories.

### 7.1 APIs

Best option when available.

Examples: GitHub API, Reddit API, Public service APIs, Official product APIs.

Advantages: structured data, lower parsing complexity, better reliability, easier incremental updates.

### 7.2 RSS / Atom

Useful for news, blogs, technical websites, release feeds, announcements.

Mochi only needs to process newly published entries.

### 7.3 Sitemaps

Useful for discovering new pages, changed pages, updated documentation, new articles.

### 7.4 HTML Extraction

Used when structured sources aren't available.

Pipeline: HTML -> remove navigation -> remove advertisements -> remove
irrelevant UI -> extract main content -> normalize text.

### 7.5 Browser Automation

Last-resort acquisition method, used for sites where content requires
JavaScript rendering, dynamic loading, interaction, or client-side
content generation. This should be carefully controlled because browser
automation is more expensive.

---

## 8. Scheduler

The Scheduler determines when each source should be checked.

Different sources have different update speeds.

Example policy:

| Source | Frequency | Strategy |
|---|---|---|
| Breaking news | 5-15 min | API/RSS |
| Reddit trends | 15-30 min | API |
| Tech blogs | 1-6 hr | RSS |
| Documentation | 6-24 hr | Sitemap/hash |
| GitHub repositories | 1-6 hr | API |
| Static references | Weekly | Conditional |
| User-selected website | On demand | Scrape |

The scheduler should avoid unnecessarily fetching sources that are unlikely to have changed.

---

## 9. Incremental Fetching

Mochi should avoid downloading and processing identical content repeatedly.

Possible mechanisms: ETag, Last-Modified, Content Hash, Document Hash, URL + Timestamp.

```text
Fetch page
    |
Check ETag / Last-Modified / hash
    |
Has content changed?
       |
       |-- NO -> Stop
       |
       `-- YES -> Process content
```

This reduces bandwidth, CPU, LLM calls, embedding generation, storage, and unnecessary processing.

---

## 10. Fetcher

The Fetcher is responsible only for obtaining source material. It should not perform complex reasoning.

Responsibilities: fetch, validate response, handle retries, respect rate limits, record HTTP metadata, store raw response.

The Fetcher should produce a normalized fetch result:

```json
{
  "url": "...",
  "status": 200,
  "retrieved_at": "...",
  "content_type": "text/html",
  "etag": "...",
  "last_modified": "...",
  "content_hash": "..."
}
```

---

## 11. Parser

The Parser converts raw source data into usable content: title, body,
author, published time, source metadata, links, media references.

The parser should preserve source provenance.

---

## 12. Normalization

Different websites represent the same information differently.

Normalization should convert content into a consistent format:

```json
{
  "title": "...",
  "content": "...",
  "author": "...",
  "source": "...",
  "url": "...",
  "published_at": "...",
  "retrieved_at": "..."
}
```

Normalization also removes navigation, repeated headers, cookie notices,
advertisements, duplicate text, and irrelevant UI elements.

---

## 13. Deduplication

The same article may appear on multiple URLs, through RSS and HTML, in
multiple feeds, or through reposts.

Mochi should detect duplicates before creating knowledge entries.

Possible methods: exact content hash + URL normalization + near-duplicate similarity.

---

## 14. Content Extraction

After normalization, Mochi extracts meaningful information: entities,
claims, topics, dates, events, relationships, keywords, summary.

---

## 15. Classification

Not everything Mochi collects should become permanent knowledge.

The classifier should separate information into at least two categories:

```text
Incoming Content
       |
 Classification
  /           \
Temporal Feed   Knowledge
   |               |
Short TTL       Long TTL
```

---

## 16. Temporal Feed

Temporal information represents things that are currently happening:
trending Reddit posts, current news, viral memes, breaking events,
current discussions, temporary internet trends.

These should have short lifetimes.

Example: a Reddit meme created 2 hours ago with a TTL of 24 hours. After
expiry, it should no longer be treated as current information.

---

## 17. Persistent Knowledge

Persistent knowledge contains information that remains useful over time:
software documentation, technical concepts, official product
information, API specifications, release information, stable reference
material.

Persistent knowledge should still be revalidated when appropriate.

---

## 18. Reddit as a v1.1 Temporal Source

Reddit can be used as an example of a high-velocity source, such as
r/popular, r/popculture, r/memes, r/dankmemes, and similar
general-audience communities, with feed behaviors like "Hot", "New", and
"Rising" ("New" being raw newly submitted content, "Rising" useful for
identifying potentially viral content early).

For Mochi, these should primarily be treated as CURRENT INTERNET CONTEXT
rather than permanent knowledge.

---

## 19. Knowledge Store

Mochi v1.1 should maintain three conceptual storage layers.

### Layer A - Raw Documents

Stores source material: id, source, url, title, content, published_at,
retrieved_at, content_hash. Purpose: provenance, debugging,
reprocessing, auditing.

### Layer B - Semantic Index

Documents are split into chunks, embedded, and placed in a vector index,
enabling semantic retrieval even when exact wording differs.

### Layer C - Structured Knowledge

Important facts and claims are stored explicitly, e.g.
`{"entity": "Python 3.15", "property": "feature", "value": "X", "source": "...", "observed_at": "...", "confidence": 0.94}`.
This is especially important for Mochi's future understanding and
hallucination-correction system.

---

## 20. Knowledge Metadata

Every stored claim/document should contain freshness and provenance
metadata: created_at, published_at, retrieved_at, last_verified_at,
expires_at, source, source_authority, confidence, freshness.

---

## 21. Freshness Engine

Freshness must become a first-class part of Mochi's retrieval system.

A result should not be ranked purely by semantic similarity. Instead:

```text
Retrieval Score = relevance + freshness + source authority + confidence
```

The exact weighting can evolve during development. The important
architectural principle is: "The most semantically similar document is
not necessarily the correct document."

---

## 22. Freshness Categories

Mochi can classify information into: FRESH, RECENT, AGING, STALE,
EXPIRED, UNKNOWN.

Freshness rules should depend on source type - a meme might expire after
hours, a software specification might remain valid for months or years.

---

## 23. Freshness Router

Before answering a question, Mochi determines whether fresh information
is required:

```text
User Question
      |
Query Understanding
      |
Freshness Classification
      |
 Stable            Current
   |                  |
Local KB          Fresh Retrieval
   |                  |
   `--------+---------'
            |
         Answer
```

---

## 24. Query Categories

- **Stable Knowledge** ("What is a transformer?") -> local knowledge + semantic retrieval.
- **Current Knowledge** ("What is the latest Python version?") -> fresh external retrieval + source verification.
- **Live Information** ("What's trending on Reddit right now?") -> live/near-live source + temporal ranking.
- **Personal Context** ("What did I tell you about my project yesterday?") -> Mochi memory + conversation context.

---

## 25. Claim Extraction

Mochi should convert important information into claims, e.g. document
"Version 5 introduces feature X." -> claim "Version 5 introduces feature X."

---

## 26. Claim Verification

Mochi should not blindly combine contradictory evidence from multiple
sources. Instead:

```text
Retrieved Evidence
        |
 Claim Extraction
        |
Supporting    Contradicting
Evidence         Evidence
   `------+--------'
          |
   Claim Resolver
          |
  Current Knowledge
```

---

## 27. Contradiction Storage

Contradictory information should not simply be deleted - store it as
evidence, along with a resolution and the reason (e.g. "Source B is
newer and more authoritative").

---

## 28. Source Authority

Not all sources should have equal weight. A conceptual authority
hierarchy: official documentation > official announcement > primary
source > established technical publication > reputable secondary source
> community discussion > random website.

Authority should be source-dependent (a GitHub repository has strong
authority for its own releases; Reddit is useful evidence of community
sentiment/trends, not authoritative technical documentation).

---

## 29. Evidence Ranking

When multiple sources provide information, Mochi should rank evidence
using: source authority + freshness + relevance + agreement with other
evidence + extraction confidence.

---

## 30. Answer Generation

The LLM should not receive the entire scraped database. Instead, the
Context Engine should provide a compact evidence package, e.g.:

```text
QUERY: "What is happening with X?"

RELEVANT CLAIMS
1. X announced Y (confidence 0.94, source: official, age: 2 hours)
2. Users are reporting Z (confidence 0.71, source: Reddit, age: 35 minutes)
3. Older information says A (confidence 0.89, age: 6 months, status: potentially outdated)
```

This prevents context bloat, irrelevant retrieved text, outdated
information dominating answers, and excessive token usage.

---

## 31. Provenance

Every important answer should be traceable back to its source: answer ->
claim -> evidence -> document -> URL/source. This makes Mochi's answers
more trustworthy and makes debugging hallucinations much easier.

---

## 32. Context Engine Integration

The Web Knowledge Engine should become one component of Mochi's larger
Context Engine, alongside personal memory and local/web knowledge, feeding
a single context assembly step before answer generation.

---

## 33. Context Priority

Mochi should combine user context + conversation context + long-term
memory + local/web knowledge + fresh web retrieval, in that general
order of precedence, before generating an answer.

---

## Primary sources referenced by the original spec

- Reddit's public read-only endpoints (no login/API key required for
  the subreddit `.json` feeds this engine and `app/humor/subreddit_crawler.py`
  both use)
- Google News' public RSS feed (already used by `app/humor/trend_fetcher.py`)
