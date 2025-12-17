You are extending an existing production-grade Persian RSS scraping system.

Current state:
- MehrNews RSS scraper is fully implemented and stable
- History, XML output, image download, and service mode all work
- MehrNews must remain unchanged

========================
NEW SOURCE
========================
Site name: Tasnim News Agency
Language: Persian (fa-IR)
RSS URL:
https://www.tasnimnews.com/fa/rss/feed/0/0/8/1/TopStories

========================
ABSOLUTE RULES
========================
- Do NOT modify MehrNews behavior
- Do NOT change XML format
- Do NOT change history schema
- Do NOT mix selectors between sites
- Tasnim logic must be fully isolated

========================
ARCHITECTURE REQUIREMENT
========================
1. Introduce a Tasnim-specific scraper module
2. Reuse shared components:
   - HistoryManager
   - ImageDownloader
   - XMLWriter
   - Service loop
3. Site-specific logic must live ONLY inside Tasnim module

========================
TASNIM RSS PROCESSING
========================
From RSS extract:
- title
- link
- guid
- pubDate
- description
- category (if exists)

========================
TASNIM ARTICLE EXTRACTION
========================
From article HTML extract:
- title
- full body (HTML preserved)
- main image (if exists)

HTML extraction rules:
- Prefer <article> tag
- Fallback to content sections
- Remove scripts and styles
- Preserve paragraph structure
- Do NOT inline images into body

========================
IMAGE RULES
========================
- Download only ONE main image
- Ignore logos, icons, avatars
- Store image next to XML
- Filename based on news ID

========================
DEDUP RULES
========================
- Use canonical URL as unique key
- History must prevent reprocessing
- Re-runs must not re-download articles

========================
FILE STRUCTURE
========================
/sources/
 ├── mehrnews.py
 ├── tasnimnews.py

========================
DELIVERABLES
========================
1. tasnimnews_scraper.py (or tasnimnews.py)
2. Integrated into existing service loop
3. Example generated Tasnim XML
4. Proof that MehrNews still works unchanged

========================
QUALITY RULES
========================
- Async only
- Production-ready
- Clean code
- No pseudo-code
- No breaking changes

Now implement Tasnim News RSS scraper following the existing system patterns.
