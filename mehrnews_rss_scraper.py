#!/usr/bin/env python3
"""
Generic RSS Scraping Engine
Production-grade Persian news scraper supporting multiple RSS sources.
"""

import asyncio
import sqlite3
import hashlib
import os
import re
import json
import signal
import sys
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, urljoin
from lxml import etree as ET
import email.utils

import httpx
import feedparser

# Import sources from separate modules
from sources.base import BaseRSSSource
from sources import mehrnews, tasnimnews, farsnews, irna, isna


# Configuration - defaults
DEFAULT_CONFIG = {
    'rss_interval_minutes': 15,
    'data_dir': 'data',
    'timeout': 30.0,
    'retry_count': 3,
    'retry_delay': 2.0,
    'user_agent': 'RSSScraper/1.0',
    'service_mode': False,
    'sources': {
        'mehrnews': {
            'enabled': True,
            'rss_url': 'https://www.mehrnews.com/rss'
        }
    }
}


class Config:
    """Configuration manager - reads from env vars and config file."""
    
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self._load_from_env()
        self._load_from_file()
    
    def _load_from_env(self):
        """Load configuration from environment variables."""
        self.config['rss_interval_minutes'] = int(os.getenv('RSS_INTERVAL_MINUTES', self.config['rss_interval_minutes']))
        self.config['data_dir'] = os.getenv('DATA_DIR', self.config['data_dir'])
        self.config['timeout'] = float(os.getenv('TIMEOUT', self.config['timeout']))
        self.config['retry_count'] = int(os.getenv('RETRY_COUNT', self.config['retry_count']))
        self.config['retry_delay'] = float(os.getenv('RETRY_DELAY', self.config['retry_delay']))
        self.config['user_agent'] = os.getenv('USER_AGENT', self.config['user_agent'])
        self.config['service_mode'] = os.getenv('SERVICE_MODE', '').lower() in ('true', '1', 'yes')
    
    def _load_from_file(self):
        """Load configuration from config.json if it exists."""
        config_file = Path('config.json')
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    self.config.update(file_config)
            except Exception:
                pass
    
    def get(self, key: str, default=None):
        """Get configuration value."""
        return self.config.get(key, default)


class StructuredLogger:
    """Structured JSON logging."""
    
    def __init__(self):
        self.job_id = None
    
    def _log(self, level: str, message: str, source_name: str = None, **kwargs):
        """Internal logging method."""
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': level,
            'message': message,
            'source': source_name or 'rss_scraper',
            **kwargs
        }
        if self.job_id:
            log_entry['job_id'] = self.job_id
        print(json.dumps(log_entry, ensure_ascii=False))
    
    def info(self, message: str, url: str = None, source_name: str = None, **kwargs):
        """Log INFO level message."""
        if url:
            kwargs['url'] = url
        self._log('INFO', message, source_name=source_name, **kwargs)
    
    def warning(self, message: str, url: str = None, source_name: str = None, **kwargs):
        """Log WARNING level message."""
        if url:
            kwargs['url'] = url
        self._log('WARNING', message, source_name=source_name, **kwargs)
    
    def error(self, message: str, url: str = None, source_name: str = None, **kwargs):
        """Log ERROR level message."""
        if url:
            kwargs['url'] = url
        self._log('ERROR', message, source_name=source_name, **kwargs)


# Global logger instance
logger = StructuredLogger()

# Global config instance
config = Config()

# Global shutdown flag for graceful shutdown
shutdown_requested = False

# Set logger in source modules
mehrnews.set_logger(logger)
tasnimnews.set_logger(logger)
farsnews.set_logger(logger)


class HistoryManager:
    """Manages persistent history of processed news items."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with history schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                url TEXT PRIMARY KEY,
                guid TEXT,
                published_date TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    
    def is_processed(self, url: str) -> bool:
        """Check if URL has been processed before."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM history WHERE url = ?", (url,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def mark_processed(self, url: str, guid: Optional[str], published_date: str):
        """Mark a news item as processed."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO history (url, guid, published_date, processed_at)
            VALUES (?, ?, ?, ?)
        """, (url, guid, published_date, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()




class ImageDownloader:
    """Handles downloading and storing article images."""
    
    def __init__(self, timeout: float, retry_count: int, retry_delay: float):
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
    
    def _get_extension_from_content_type(self, content_type: Optional[str]) -> Optional[str]:
        """Extract file extension from content type."""
        if not content_type:
            return None
        mapping = {
            'image/jpeg': '.jpg',
            'image/jpg': '.jpg',
            'image/png': '.png',
            'image/gif': '.gif',
            'image/webp': '.webp',
        }
        return mapping.get(content_type.split(';')[0].strip().lower())
    
    async def download_image(self, url: str, news_code: str, day_dir: Path, client: httpx.AsyncClient) -> Optional[str]:
        """Download image and save it with news code as filename. Return filename."""
        for attempt in range(self.retry_count):
            try:
                response = await client.get(url, follow_redirects=True, timeout=self.timeout)
                response.raise_for_status()
                
                content_type = response.headers.get('content-type', '')
                ext = self._get_extension_from_content_type(content_type) or '.jpg'
                filename = f"{news_code}{ext}"
                filepath = day_dir / filename
                
                # Atomic write: write to temp file first, then rename
                temp_file = filepath.with_suffix(f'.tmp{ext}')
                try:
                    with open(temp_file, 'wb') as f:
                        f.write(response.content)
                    # Atomic rename
                    temp_file.replace(filepath)
                    return filename
                except Exception as e:
                    if temp_file.exists():
                        temp_file.unlink()
                    raise e
                
            except Exception as e:
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                logger.error(f"Error downloading image after {self.retry_count} attempts", url=url, error=str(e))
                return None


class XMLWriter:
    """Writes news articles to XML files."""
    
    def __init__(self, articles_dir: Path):
        self.articles_dir = articles_dir
    
    def _parse_date(self, date_str: str) -> str:
        """Parse RSS date and convert to format: 2024-05-05 18:00"""
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d %H:%M")
        
        try:
            # Try parsing with feedparser's date parser
            parsed_time = email.utils.parsedate_tz(date_str)
            if parsed_time:
                dt = datetime(*parsed_time[:6])
                return dt.strftime("%Y-%m-%d %H:%M")
        except:
            pass
        
        # Fallback to current time
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    
    def write_article(self, rss_item: Dict[str, Any], article_data: Dict[str, Any], news_code: str, day_dir: Path, source_name: str, xml_config: Dict[str, Any]) -> str:
        """Write article to XML file in day folder using atomic write. Return filename."""
        # Use news code as filename
        filename = f"{news_code}.xml"
        filepath = day_dir / filename
        temp_file = filepath.with_suffix('.tmp.xml')
        
        try:
            # Get body and append URL at the end
            body_text = article_data.get('body', '')
            news_url = rss_item.get('link', '')
            if news_url:
                body_text = f"{body_text}\n\n{news_url}"
            
            # Create XML structure
            root = ET.Element('IRIB.Exporter.AirNewsXml')
            item = ET.SubElement(root, 'item')
            
            # Title
            ET.SubElement(item, 'title').text = rss_item.get('title', '')
            
            # Subtitle (using category or empty)
            subtitle = rss_item.get('category', 'FSD')
            ET.SubElement(item, 'subtitle').text = subtitle if subtitle else 'FSD'
            
            # Body as HTML (in CDATA) with URL appended
            body_elem = ET.SubElement(item, 'bodyAsHtml')
            body_elem.text = ET.CDATA(body_text)
            
            # Summary (using description from RSS)
            summary = rss_item.get('description', article_data.get('body', ''))
            # Truncate summary if too long
            if len(summary) > 500:
                summary = summary[:500] + '...'
            ET.SubElement(item, 'summary').text = summary
            
            # Parse and format date
            date_str = self._parse_date(rss_item.get('pubDate', ''))
            ET.SubElement(item, 'createDate').text = date_str
            ET.SubElement(item, 'eventDate').text = date_str
            
            # Language ID
            language_id = xml_config.get('languageId', '1')
            ET.SubElement(item, 'languageId').text = str(language_id) if language_id else ''
            
            # Priority ID
            priority_id = xml_config.get('priorityId', '')
            priority_elem = ET.SubElement(item, 'priorityId')
            if priority_id:
                priority_elem.text = str(priority_id)
            
            # Organizational Unit ID
            org_unit_id = xml_config.get('organizationalUnitId', '')
            org_elem = ET.SubElement(item, 'organizationalUnitId')
            if org_unit_id:
                org_elem.text = str(org_unit_id)
            
            # Security ID
            security_id = xml_config.get('securityId', '')
            security_elem = ET.SubElement(item, 'securityId')
            if security_id:
                security_elem.text = str(security_id)
            
            # News Type ID
            news_type_id = xml_config.get('newsTypeId', '')
            news_type_elem = ET.SubElement(item, 'newsTypeId')
            if news_type_id:
                news_type_elem.text = str(news_type_id)
            
            # News Agency ID
            news_agency_id = xml_config.get('newsAgencyId', '1')
            ET.SubElement(item, 'newsAgencyId').text = str(news_agency_id) if news_agency_id else ''
            
            # Resource ID
            resource_id = xml_config.get('resourceId', '')
            resource_elem = ET.SubElement(item, 'resourceId')
            if resource_id:
                resource_elem.text = str(resource_id)
            
            # Owner ID
            owner_id = xml_config.get('ownerId', '')
            owner_elem = ET.SubElement(item, 'ownerId')
            if owner_id:
                owner_elem.text = str(owner_id)
            
            # Reporter ID
            reporter_id = xml_config.get('reporterId', '')
            reporter_elem = ET.SubElement(item, 'reporterId')
            if reporter_id:
                reporter_elem.text = str(reporter_id)
            
            # Translator ID
            translator_id = xml_config.get('translatorId', '')
            translator_elem = ET.SubElement(item, 'translatorId')
            if translator_id:
                translator_elem.text = str(translator_id)
            
            # Producer
            producer = xml_config.get('producer', '12')
            ET.SubElement(item, 'producer').text = str(producer) if producer else ''
            
            # Action Status ID
            action_status_id = xml_config.get('actionStatusId', '')
            action_status_elem = ET.SubElement(item, 'actionStatusId')
            if action_status_id:
                action_status_elem.text = str(action_status_id)
            
            # Source ID
            source_id = xml_config.get('sourceId', '12')
            ET.SubElement(item, 'sourceId').text = str(source_id) if source_id else ''
            
            # Source Type
            source_type = xml_config.get('sourceType', '')
            source_type_elem = ET.SubElement(item, 'sourceType')
            if source_type:
                source_type_elem.text = str(source_type)
            
            # Destination Type
            destination_type = xml_config.get('destinationType', '')
            destination_type_elem = ET.SubElement(item, 'destinationType')
            if destination_type:
                destination_type_elem.text = str(destination_type)
            
            # Keywords (using category if available)
            keywords = rss_item.get('category', '')
            ET.SubElement(item, 'keywords').text = keywords if keywords else ''
            
            # News Categories
            news_categories = ET.SubElement(item, 'newsCategories')
            news_category_id = xml_config.get('newsCategories', {}).get('id', '12')
            ET.SubElement(news_categories, 'id').text = str(news_category_id) if news_category_id else ''
            
            # Atomic write: write to temp file first, then rename
            tree = ET.ElementTree(root)
            tree.write(temp_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
            # Atomic rename
            temp_file.replace(filepath)
            
            return filename
        except Exception as e:
            # Clean up temp file on error
            if temp_file.exists():
                temp_file.unlink()
            raise e


class RSSEngine:
    """Generic RSS scraping engine that works with any BaseRSSSource."""
    
    def __init__(self, source: BaseRSSSource, cfg: Config, source_config: Dict[str, Any]):
        self.source = source
        self.config = cfg
        self.source_name = source.source_name
        self.source_config = source_config
        
        # Get XML config from source config with defaults (merge defaults with provided config)
        default_xml_config = {
            'languageId': '1',
            'priorityId': '',
            'organizationalUnitId': '',
            'securityId': '',
            'newsTypeId': '',
            'newsAgencyId': '1',
            'resourceId': '',
            'ownerId': '',
            'reporterId': '',
            'translatorId': '',
            'producer': '12',
            'actionStatusId': '',
            'sourceId': '12',
            'sourceType': '',
            'destinationType': '',
            'newsCategories': {'id': '12'}
        }
        provided_xml_config = source_config.get('xml_config', {})
        # Merge defaults with provided config
        self.xml_config = default_xml_config.copy()
        self.xml_config.update(provided_xml_config)
        # Handle newsCategories separately to merge nested dict
        if 'newsCategories' in provided_xml_config:
            self.xml_config['newsCategories'] = {
                'id': provided_xml_config['newsCategories'].get('id', '12')
            }
        
        # Per-source data directory
        data_dir = Path(cfg.get('data_dir'))
        self.source_data_dir = data_dir / self.source_name
        self.articles_dir = self.source_data_dir / "articles"
        self.history_db = self.source_data_dir / "history.db"
        
        # Ensure directories exist
        self.source_data_dir.mkdir(parents=True, exist_ok=True)
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        
        self.history = HistoryManager(self.history_db)
        self.image_downloader = ImageDownloader(
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
        self.xml_writer = XMLWriter(self.articles_dir)
    
    async def process_item(self, item: Dict[str, Any], client: httpx.AsyncClient) -> bool:
        """Process a single RSS item if it's new. Errors are isolated and logged."""
        url = item.get('link', '')
        if not url:
            return False
        
        try:
            # Check if already processed
            if self.history.is_processed(url):
                return False
            
            # Extract news code from URL
            news_code = self.source.extract_news_code(url)
            
            # Create day folder (YYYY-MM-DD format) - uses current date at processing time
            # This ensures that when day changes during service operation, new articles go to the new day's folder
            day_folder = datetime.now().strftime("%Y-%m-%d")
            day_dir = self.articles_dir / day_folder
            day_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract article content using source
            article_data = await self.source.extract_article(url, client)
            
            # Skip articles with empty or invalid body (e.g., JavaScript requirement pages)
            body = article_data.get('body', '').strip()
            if not body or len(body) < 50:
                logger.warning(f"Skipping article with empty/invalid body", url=url, source_name=self.source_name, body_length=len(body) if body else 0)
                # Still mark as processed to avoid retrying
                self.history.mark_processed(
                    url=url,
                    guid=item.get('guid', ''),
                    published_date=item.get('pubDate', '')
                )
                return False
            
            # Download main image if present
            main_image_filename = None
            if article_data.get('main_image_url'):
                main_image_filename = await self.image_downloader.download_image(
                    article_data['main_image_url'], news_code, day_dir, client
                )
            
            # Merge article title if extracted
            if article_data.get('title'):
                item['title'] = article_data['title']
            
            # Prepare article data for XML writer
            xml_article_data = {
                'body': body,
                'main_image': main_image_filename
            }
            
            # Write XML (atomic write)
            self.xml_writer.write_article(item, xml_article_data, news_code, day_dir, self.source_name, self.xml_config)
            
            # Mark as processed (must happen after successful write)
            self.history.mark_processed(
                url=url,
                guid=item.get('guid', ''),
                published_date=item.get('pubDate', '')
            )
            
            logger.info(f"New article saved", url=url, news_code=news_code, source_name=self.source_name)
            return True
        except Exception as e:
            # Error isolation: one failed article must NOT stop the loop
            logger.error(f"Failed to process article", url=url, source_name=self.source_name, error=str(e))
            return False
    
    async def run_once(self):
        """Run one scraping cycle for this source."""
        user_agent = self.config.get('user_agent')
        async with httpx.AsyncClient(headers={'User-Agent': user_agent}) as client:
            # Log appropriate message based on source type
            if self.source_name == 'farsnews':
                logger.info(f"Fetching listing page", url=self.source.rss_url, source_name=self.source_name)
            else:
                logger.info(f"Fetching RSS feed", url=self.source.rss_url, source_name=self.source_name)
            items = await self.source.fetch_rss_items(client)
            if self.source_name == 'farsnews':
                logger.info(f"Found {len(items)} items in listing page", source_name=self.source_name)
            else:
                logger.info(f"Found {len(items)} items in RSS feed", source_name=self.source_name)
            
            # Process new items (errors are isolated per item)
            processed_count = 0
            for item in items:
                if shutdown_requested:
                    break
                if await self.process_item(item, client):
                    processed_count += 1
            
            logger.info(f"Completed scraping cycle", processed_count=processed_count, total_items=len(items), source_name=self.source_name)
            return processed_count


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    shutdown_requested = True
    logger.info("Shutdown signal received, finishing current cycle...")


def create_source(source_name: str, source_config: Dict[str, Any], cfg: Config) -> Optional[BaseRSSSource]:
    """Factory function to create source instances."""
    if source_name == 'mehrnews':
        return mehrnews.MehrNewsSource(
            rss_url=source_config.get('rss_url', 'https://www.mehrnews.com/rss'),
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
    elif source_name == 'tasnimnews':
        return tasnimnews.TasnimSource(
            listing_url=source_config.get('listing_url', 'https://www.tasnimnews.com/fa/archive'),
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
    elif source_name == 'farsnews':
        # Fars News is a SPA; use the search page as the listing source by default
        # Example: https://farsnews.ir/search
        return farsnews.FarsSource(
            listing_url=source_config.get('listing_url', 'https://farsnews.ir/search'),
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
    elif source_name == 'irna':
        return irna.IRNASource(
            rss_url=source_config.get('rss_url', 'https://www.irna.ir/rss'),
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
    elif source_name == 'isna':
        return isna.ISNASource(
            listing_url=source_config.get('listing_url', 'https://www.isna.ir/page/archive.xhtml'),
            timeout=cfg.get('timeout'),
            retry_count=cfg.get('retry_count'),
            retry_delay=cfg.get('retry_delay')
        )
    return None


async def service_loop(engines: List[RSSEngine]):
    """Long-running service loop for multiple sources."""
    interval_minutes = config.get('rss_interval_minutes')
    interval_seconds = interval_minutes * 60
    
    logger.info("Starting service mode", interval_minutes=interval_minutes)
    
    while not shutdown_requested:
        try:
            for engine in engines:
                if shutdown_requested:
                    break
                await engine.run_once()
            
            if shutdown_requested:
                break
            
            # Wait for next cycle
            logger.info(f"Waiting {interval_minutes} minutes until next cycle")
            for _ in range(interval_seconds):
                if shutdown_requested:
                    break
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in service loop", error=str(e))
            if not shutdown_requested:
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    logger.info("Service loop stopped")
    
    # Cleanup: Close browser resources for sources that use them (e.g., Fars News)
    for engine in engines:
        if hasattr(engine.source, 'cleanup'):
            try:
                await engine.source.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up source {engine.source_name}: {str(e)}")


async def main():
    """Main entry point - supports one-shot and service mode with multiple sources."""
    global shutdown_requested
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create engines for enabled sources
    engines = []
    sources_config = config.get('sources', {})
    sources = []  # Keep reference to sources for cleanup
    
    for source_name, source_config in sources_config.items():
        if source_config.get('enabled', True):
            source = create_source(source_name, source_config, config)
            if source:
                sources.append(source)
                engines.append(RSSEngine(source, config, source_config))
            else:
                logger.warning(f"Unknown source: {source_name}")
    
    if not engines:
        logger.error("No enabled sources found")
        return
    
    try:
        if config.get('service_mode'):
            # Service mode: run forever
            await service_loop(engines)
        else:
            # One-shot execution
            for engine in engines:
                await engine.run_once()
    finally:
        # Cleanup: Close browser resources for sources that use them (e.g., Fars News)
        for source in sources:
            if hasattr(source, 'cleanup'):
                try:
                    await source.cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up source {source.source_name}: {str(e)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

