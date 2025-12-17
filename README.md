# Generic RSS Scraping Engine

Production-grade generic RSS scraping engine supporting multiple Persian news sources.

## Installation

```bash
pip install -r requirements.txt
```

### Playwright Installation (Required for Fars News)

Fars News requires a headless browser to render JavaScript content. After installing requirements, install Playwright browsers:

```bash
playwright install chromium
```

This will download the Chromium browser needed for Fars News scraping.

## Usage

### One-Shot Execution

Run the scraper once and exit:

```bash
python mehrnews_rss_scraper.py
```

### Service Mode (Long-Running)

Run as a long-running service that polls RSS at regular intervals:

```bash
# Using environment variable
SERVICE_MODE=true python mehrnews_rss_scraper.py

# Or set in config.json
```

The service will:
- Poll RSS feed every N minutes (configurable)
- Run until interrupted (SIGTERM/SIGINT)
- Gracefully finish current cycle before shutdown

## Configuration

Configuration can be provided via:
1. **Environment variables** (highest priority)
2. **config.json** file (copy from `config.json.example`)
3. **Default values** (fallback)

### Environment Variables

- `RSS_INTERVAL_MINUTES`: Polling interval in minutes (default: 15)
- `DATA_DIR`: Base output directory (default: data)
- `TIMEOUT`: HTTP timeout in seconds (default: 30.0)
- `RETRY_COUNT`: Number of retries for failed requests (default: 3)
- `RETRY_DELAY`: Delay between retries in seconds (default: 2.0)
- `USER_AGENT`: HTTP User-Agent header (default: RSSScraper/1.0)
- `SERVICE_MODE`: Enable service mode (true/false, default: false)

### Config File

Copy `config.json.example` to `config.json` and customize:

```json
{
  "rss_interval_minutes": 15,
  "data_dir": "data",
  "timeout": 30.0,
  "retry_count": 3,
  "retry_delay": 2.0,
  "user_agent": "RSSScraper/1.0",
  "service_mode": false,
  "sources": {
    "mehrnews": {
      "enabled": true,
      "rss_url": "https://www.mehrnews.com/rss"
    }
  }
}
```

Each source can be enabled/disabled individually via the `enabled` flag.

## Logging

The scraper uses structured JSON logging. Each log entry includes:
- `timestamp`: ISO 8601 timestamp
- `level`: INFO, WARNING, or ERROR
- `message`: Human-readable message
- `source`: Source name (e.g., "mehrnews", "rss_scraper")
- `url`: Article URL (when applicable)
- `job_id`: Job identifier (when applicable)

Example log entry:
```json
{"timestamp": "2025-01-15T10:30:00", "level": "INFO", "message": "New article saved", "source": "mehrnews", "url": "https://www.mehrnews.com/news/1234567/..."}
```

## Error Handling

- **Error Isolation**: One failed article does not stop processing of other articles
- **Retry Logic**: Network errors are automatically retried (configurable)
- **Malformed Items**: Invalid RSS entries are skipped safely
- **Atomic Writes**: File writes are atomic to prevent corruption
- **History Safety**: History database operations are safe and never lost

## Deduplication System

The scraper implements a persistent deduplication system to prevent processing the same news articles multiple times.

### How It Works

1. **History Storage**: The system maintains per-source SQLite databases (e.g., `data/mehrnews/history.db`) that store a record of every processed news item.

2. **Unique Identification**: Each news item is uniquely identified by its canonical URL (the `link` field from the RSS feed).

3. **Pre-Processing Check**: Before processing any RSS item:
   - The scraper checks if the item's URL exists in the history database
   - If the URL is found → the item is skipped completely
   - If the URL is not found → the item is processed as new

4. **Post-Processing Record**: After successfully processing a new item:
   - The URL, GUID, published date, and processing timestamp are saved to the history database
   - This ensures the item will be skipped in future runs

5. **Persistence**: The history database survives restarts, so the scraper can be run multiple times without reprocessing articles.

### History Schema

The history database contains the following fields:
- `url` (PRIMARY KEY): The canonical URL of the news article
- `guid`: The GUID from the RSS feed (if available)
- `published_date`: The raw published date string from RSS
- `processed_at`: Timestamp when the article was processed

### Benefits

- **Idempotent Execution**: The scraper can be run repeatedly without creating duplicate articles
- **Efficient**: Only new articles trigger full processing (HTML fetching, image downloading)
- **Reliable**: History persists across restarts and system reboots
- **Fast Lookups**: SQLite provides efficient URL lookups even with large history

## Architecture

The scraper uses a generic engine architecture:

- **BaseRSSSource**: Abstract base class defining the contract for RSS sources
- **RSSEngine**: Generic engine that handles history, XML writing, image downloading, logging, and error handling
- **Source Implementations**: Site-specific classes (e.g., `MehrNewsSource`) that implement RSS parsing and HTML extraction

### Folder Structure

```
data/
├── mehrnews/
│   ├── history.db
│   └── articles/
│       └── 2025-01-15/
│           ├── 1234567.xml
│           └── 1234567.jpg
└── [other-source]/
    ├── history.db
    └── articles/
        └── ...
```

Each source has its own isolated directory with history and articles.

## Adding a New Source

To add a new RSS source:

1. **Create a new source class** inheriting from `BaseRSSSource`:

```python
class MyNewsSource(BaseRSSSource):
    @property
    def source_name(self) -> str:
        return 'mynews'
    
    @property
    def rss_url(self) -> str:
        return 'https://example.com/rss'
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        # Implement RSS parsing logic
        pass
    
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        # Implement article HTML extraction
        # Return: {'title': str, 'body': str (HTML), 'main_image_url': str or None}
        pass
    
    def extract_main_image(self, html: str, base_url: str) -> Optional[str]:
        # Implement main image extraction from HTML
        # Return: image URL or None
        pass
```

2. **Register the source** in the `create_source()` function:

```python
def create_source(source_name: str, source_config: Dict[str, Any], cfg: Config) -> Optional[BaseRSSSource]:
    if source_name == 'mehrnews':
        return MehrNewsSource(...)
    elif source_name == 'mynews':
        return MyNewsSource(...)
    return None
```

3. **Add to configuration**:

```json
{
  "sources": {
    "mehrnews": {
      "enabled": true,
      "rss_url": "https://www.mehrnews.com/rss"
    },
    "mynews": {
      "enabled": true,
      "rss_url": "https://example.com/rss"
    }
  }
}
```

The engine will automatically:
- Create isolated directories for each source
- Maintain separate history databases
- Process all enabled sources in each cycle

