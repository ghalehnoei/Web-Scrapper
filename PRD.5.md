You are extending a production-grade Persian news scraping system.

Current system:
- MehrNews (RSS-based)
- TasnimNews (RSS-based)
- Stable history, XML writer, image downloader, service loop

You must add a NEW source with a DIFFERENT ingestion model.

========================
NEW SOURCE TYPE
========================
Site name: Fars News Agency
Source type: Listing Page (NO RSS)
Listing URL:
https://farsnews.ir/showcase

Language: Persian (fa-IR)

========================
ABSOLUTE RULES (CRITICAL)
========================
- Do NOT assume RSS exists
- Do NOT add RSS logic for Fars
- Do NOT modify MehrNews or Tasnim
- Do NOT change XML format
- Do NOT change history schema
- Fars logic must be fully isolated

========================
INGESTION MODEL
========================
1. Fetch the listing page (/showcase)
2. Extract latest article URLs
3. Deduplicate URLs within the page
4. For each URL:
   - Check history
   - If new → process
   - If exists → skip

========================
LISTING PAGE EXTRACTION
========================
From https://farsnews.ir/showcase extract:
- Canonical article URLs only
- URLs matching pattern:
  /news/{numeric_id}
- Ignore:
  - videos
  - galleries
  - live streams
  - ads
  - external links

Selectors must be:
- Resilient
- Non-index-based
- CSS preferred, XPath fallback

========================
ARTICLE PAGE EXTRACTION
========================
From article HTML extract:
- title
- full body (HTML preserved)
- main image (if exists)

Extraction rules:
- Prefer <article> tag
- Fallback selectors:
    - .news-body
    - .nt-body
    - .content
- Remove:
    - script
    - style
- Preserve paragraphs and inline tags

========================
IMAGE EXTRACTION
========================
- Download only ONE main image
- Prefer:
    - og:image
    - article header image
- Skip logos, icons, ads
- Image filename based on numeric news ID

========================
DEDUPLICATION
========================
- Use article URL as unique identifier
- History must prevent reprocessing
- Re-running the scraper must NOT re-download old news

========================
INTEGRATION REQUIREMENTS
========================
- Fars source must plug into existing service loop
- It must run on the same schedule
- Errors must be isolated per article

========================
FILE STRUCTURE
========================
/sources/
 ├── mehrnews.py
 ├── tasnimnews.py
 ├── farsnews_showcase.py

========================
DELIVERABLES
========================
1. farsnews_showcase_scraper.py
2. Listing page URL extractor
3. Article extractor
4. Example generated XML
5. Proof Mehr & Tasnim still work

========================
QUALITY RULES
========================
- Python 3.11
- Async only
- Production-grade
- No pseudo-code
- Clear docstrings

FINAL RULE:
Only EXTEND the system.
Never refactor unrelated code.
