You are refactoring an existing production-grade Persian RSS scraper.

Current state:
- Fully working MehrNews RSS scraper
- History, XML, images, service mode all work correctly

DO NOT break existing MehrNews functionality.

========================
NEW GOAL
========================
Convert the system into a GENERIC RSS scraping engine
that supports multiple Persian news sources.

========================
ARCHITECTURE CHANGES
========================
1. Introduce a BaseRSSSource abstraction
2. MehrNews must become one implementation of BaseRSSSource
3. Core engine must NOT contain site-specific logic

========================
BaseRSSSource CONTRACT
========================
Each source must define:
- source_name
- rss_url
- extract_article(url)
- extract_main_image(html)

========================
ENGINE RESPONSIBILITIES
========================
- Service loop
- History management
- XML writing
- Image downloading
- Logging
- Error handling

========================
SOURCE RESPONSIBILITIES
========================
- RSS parsing
- HTML extraction logic
- Site-specific selectors

========================
CONFIGURATION
========================
- Support enabling/disabling sources via config
- Each source config isolated

========================
DELIVERABLES
========================
1. BaseRSSSource abstract class
2. Refactored MehrNewsSource implementation
3. Core engine independent of MehrNews
4. Updated folder structure
5. README explaining how to add a new site

========================
STRICT RULES
========================
- MehrNews behavior must remain identical
- XML format must NOT change
- History logic must stay correct
- Generate real production-ready code
