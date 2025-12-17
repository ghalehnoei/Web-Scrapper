"""IRNA (Islamic Republic News Agency) RSS source implementation."""

import asyncio
import hashlib
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import httpx
import feedparser
from bs4 import BeautifulSoup

from sources.base import BaseRSSSource


# Global logger - will be set by main module
logger = None


def set_logger(logger_instance):
    """Set the logger instance for this module."""
    global logger
    logger = logger_instance


class IRNASource(BaseRSSSource):
    """IRNA (Islamic Republic News Agency) RSS source implementation."""
    
    def __init__(self, rss_url: str, timeout: float, retry_count: int, retry_delay: float):
        self._rss_url = rss_url
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        # Playwright browser for JavaScript-rendered content (IRNA uses CDN redirects)
        self._playwright = None
        self._browser = None
        self._browser_context = None
    
    @property
    def source_name(self) -> str:
        return 'irna'
    
    @property
    def rss_url(self) -> str:
        return self._rss_url
    
    def extract_news_code(self, url: str) -> str:
        """Extract news code from IRNA URL."""
        # IRNA URLs have structure like:
        # https://www.irna.ir/news/86028063/slug-text
        # Pattern: /news/{numeric_id}/{slug}
        # The numeric ID is typically 8 digits
        
        # Extract numeric ID from /news/{id}/ pattern
        match = re.search(r'/news/(\d+)', url)
        if match:
            return match.group(1)
        
        # Fallback: use hash of URL
        return hashlib.md5(url.encode()).hexdigest()[:8]
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch and parse IRNA RSS feed."""
        for attempt in range(self.retry_count):
            try:
                response = await client.get(self._rss_url, timeout=self.timeout)
                response.raise_for_status()
                
                feed = feedparser.parse(response.text)
                items = []
                
                for entry in feed.entries:
                    try:
                        item = {
                            'title': entry.get('title', ''),
                            'link': entry.get('link', ''),
                            'guid': entry.get('id', entry.get('link', '')),
                            'pubDate': entry.get('published', ''),
                            'description': entry.get('description', ''),
                            'category': ''
                        }
                        
                        if hasattr(entry, 'tags') and entry.tags:
                            item['category'] = ', '.join([tag.get('term', '') for tag in entry.tags])
                        
                        items.append(item)
                    except Exception as e:
                        if logger:
                            logger.warning(f"Skipping malformed RSS entry", source_name=self.source_name, error=str(e))
                        continue
                
                return items
            except Exception as e:
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                if logger:
                    logger.error(f"Error fetching RSS feed after {self.retry_count} attempts", url=self._rss_url, source_name=self.source_name, error=str(e))
                return []
    
    async def _get_browser_context(self):
        """Get or create a Playwright browser context for JavaScript rendering."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            if logger:
                logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium", source_name=self.source_name)
            return None, None
        
        try:
            if self._playwright is None:
                if logger:
                    logger.info("Initializing Playwright browser for IRNA...", source_name=self.source_name)
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
                )
                self._browser_context = await self._browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='fa-IR',
                    timezone_id='Asia/Tehran'
                )
                if logger:
                    logger.info("Playwright browser initialized successfully for IRNA", source_name=self.source_name)
            
            return self._browser_context, self._playwright
        except Exception as e:
            if logger:
                logger.error(f"Failed to initialize Playwright browser: {str(e)}. Make sure Playwright is installed: pip install playwright && playwright install chromium", source_name=self.source_name, error=str(e))
            return None, None
    
    async def cleanup(self):
        """Clean up browser resources. Should be called when done with the source."""
        if self._browser_context:
            try:
                await self._browser_context.close()
            except Exception as e:
                if logger:
                    logger.warning(f"Error closing browser context: {str(e)}", source_name=self.source_name)
            self._browser_context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                if logger:
                    logger.warning(f"Error closing browser: {str(e)}", source_name=self.source_name)
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                if logger:
                    logger.warning(f"Error stopping Playwright: {str(e)}", source_name=self.source_name)
            self._playwright = None
    
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Extract article content from IRNA HTML page using headless browser for CDN redirects."""
        page = None
        for attempt in range(self.retry_count):
            try:
                # Use headless browser for JavaScript-rendered content (IRNA uses CDN redirects)
                html = None
                browser_context, playwright = await self._get_browser_context()
                if browser_context:
                    page = await browser_context.new_page()
                    try:
                        if logger:
                            logger.info(f"Using headless browser to fetch IRNA article", url=url, source_name=self.source_name)
                        await page.goto(url, wait_until='networkidle', timeout=int(self.timeout * 1000))
                        # Wait for content to load (JavaScript rendering and CDN redirects)
                        await page.wait_for_timeout(3000)  # Wait 3 seconds for JS to render
                        # Try to wait for article content to appear
                        try:
                            await page.wait_for_selector('article, .content, .news-body, [class*="content"], p', timeout=5000)
                        except:
                            pass  # Continue even if selector not found
                        html = await page.content()
                        await page.close()
                        page = None
                        if logger:
                            logger.info(f"Successfully fetched IRNA page with headless browser, HTML length: {len(html)}", url=url, source_name=self.source_name)
                    except Exception as browser_error:
                        if page:
                            try:
                                await page.close()
                            except:
                                pass
                            page = None
                        if logger:
                            logger.warning(f"Headless browser failed, attempt {attempt + 1}/{self.retry_count}: {str(browser_error)}", url=url, source_name=self.source_name)
                        if attempt < self.retry_count - 1:
                            await asyncio.sleep(self.retry_delay)
                            continue
                        # Final fallback to regular HTTP request
                        if logger:
                            logger.warning(f"Falling back to HTTP request after browser failure", url=url, source_name=self.source_name)
                        response = await client.get(url, follow_redirects=True, timeout=self.timeout)
                        response.raise_for_status()
                        html = response.text
                else:
                    # Fallback to regular HTTP request if browser not available
                    if logger:
                        logger.warning(f"Browser not available, using HTTP request (Playwright may not be installed or browser failed to initialize)", url=url, source_name=self.source_name)
                    response = await client.get(url, follow_redirects=True, timeout=self.timeout)
                    response.raise_for_status()
                    html = response.text
                
                if not html:
                    if attempt < self.retry_count - 1:
                        await asyncio.sleep(self.retry_delay)
                        continue
                    raise Exception("Failed to fetch HTML content")
                
                main_image_url = self.extract_main_image(html, url)
                
                soup = BeautifulSoup(html, 'html.parser')
                
                title = ''
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text(strip=True)
                
                body = ''
                content_container = None
                
                # Try to find content container
                body_selectors = [
                    'article',
                    '.article-body',
                    '.content',
                    '.news-body',
                    '#news-body',
                    '.text',
                    '.news-text',
                    '.article-content',
                    '.story-body'
                ]
                
                for selector in body_selectors:
                    body_elem = soup.select_one(selector)
                    if body_elem:
                        content_container = body_elem
                        break
                
                if not content_container:
                    main = soup.find('main') or soup.find('div', class_=re.compile('main|content', re.I))
                    if main:
                        content_container = main
                
                if content_container:
                    # Clean unwanted elements
                    for unwanted in content_container(['script', 'style', 'noscript', 'iframe', 'aside', 'nav', 'header', 'footer', 'form']):
                        unwanted.decompose()
                    
                    # Remove ads and related content
                    for ad in content_container.select('.ad, .advertisement, .ads, [class*="ad-"], [class*="advertisement"], [rel="sponsored"], a[rel~="sponsored"]'):
                        ad.decompose()
                    
                    # Extract paragraphs and clean them
                    body_parts = []
                    
                    # Strategy 1: Find all <p> tags and clean them
                    p_tags = content_container.find_all('p', recursive=True)
                    if p_tags:
                        for p in p_tags:
                            # Remove unwanted elements from paragraph
                            for unwanted in p(['script', 'style', 'noscript', 'iframe']):
                                unwanted.decompose()
                            
                            # Get paragraph content
                            p_html = p.decode_contents().strip()
                            p_text = p.get_text(strip=True)
                            
                            # Skip empty paragraphs or very short ones
                            if not p_text or len(p_text) < 10:
                                continue
                            
                            # Skip paragraphs that are just whitespace or single characters
                            if p_text.strip() in ['', ' ', '\n', '\t']:
                                continue
                            
                            # Get direction attribute if present
                            dir_attr = p.get('dir', 'rtl')
                            
                            # Clean up the paragraph HTML - remove extra nested divs/spans but keep links and formatting
                            # We'll keep basic formatting tags like <strong>, <em>, <a>, <br>
                            p_soup = BeautifulSoup(p_html, 'html.parser')
                            
                            # Remove unwanted nested containers but keep text and basic formatting
                            for tag in p_soup.find_all(['div', 'span']):
                                # Replace with its contents (unwrap)
                                tag.unwrap()
                            
                            cleaned_p_html = p_soup.decode_contents().strip()
                            if cleaned_p_html:
                                body_parts.append(f'<p dir="{dir_attr}">{cleaned_p_html}</p>')
                    
                    # Strategy 2: If no <p> tags found, look for divs with substantial text
                    if not body_parts:
                        div_tags = content_container.find_all('div', recursive=True)
                        for div in div_tags:
                            div_text = div.get_text(strip=True)
                            # Only consider divs with substantial text (likely paragraphs)
                            if div_text and len(div_text) > 50:
                                # Check if this div contains paragraphs (if so, skip to avoid duplicates)
                                if div.find_all('p'):
                                    continue
                                
                                # Remove unwanted elements
                                for unwanted in div(['script', 'style', 'noscript', 'iframe']):
                                    unwanted.decompose()
                                
                                # Get cleaned content
                                div_html = div.decode_contents().strip()
                                div_soup = BeautifulSoup(div_html, 'html.parser')
                                
                                # Remove nested divs/spans but keep text and basic formatting
                                for tag in div_soup.find_all(['div', 'span']):
                                    tag.unwrap()
                                
                                cleaned_div_html = div_soup.decode_contents().strip()
                                if cleaned_div_html and len(cleaned_div_html) > 50:
                                    dir_attr = div.get('dir', 'rtl')
                                    body_parts.append(f'<p dir="{dir_attr}">{cleaned_div_html}</p>')
                    
                    # Join all paragraphs
                    if body_parts:
                        body = '\n'.join(body_parts)
                    else:
                        # Fallback: use cleaned inner HTML
                        body = content_container.decode_contents()
                
                return {
                    'title': title,
                    'body': body,
                    'main_image_url': main_image_url
                }
            except Exception as e:
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                if logger:
                    logger.error(f"Error fetching article after {self.retry_count} attempts", url=url, source_name=self.source_name, error=str(e))
                return {
                    'title': '',
                    'body': '',
                    'main_image_url': None
                }
    
    def extract_main_image(self, html: str, base_url: str) -> Optional[str]:
        """Extract main image URL from IRNA HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Strategy 1: Try og:image meta tag
        og_image = soup.find('meta', property='og:image')
        if og_image:
            img_url = og_image.get('content', '')
            if img_url:
                full_url = urljoin(base_url, img_url)
                if not any(skip in full_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
                    return full_url
        
        # Strategy 2: Try IRNA-specific image selectors
        featured_selectors = [
            'img.news-image',
            '.news-image img',
            'img.lead-image',
            '.lead-image img',
            '.story-image img',
            'article img.lead',
            '.single-image img',
            'img[class*="featured"]',
            'img[class*="main"]',
            'img[class*="lead"]',
            '.article-header img',
            '.news-header img',
            'header img'
        ]
        
        for selector in featured_selectors:
            img_elem = soup.select_one(selector)
            if img_elem:
                src = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                if src:
                    img_url = urljoin(base_url, src)
                    # Prefer images from img*.irna.ir (IRNA CDN)
                    if 'irna.ir' in img_url.lower() and not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
                        return img_url
                    # Also accept other domains if they look like article images
                    if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
                        return img_url
        
        # Strategy 3: Find first large image in article
        article = soup.find('article') or soup.find('div', class_=re.compile('article|content|news', re.I))
        if article:
            img_tags = article.find_all('img', limit=10)
        else:
            img_tags = soup.find_all('img', limit=10)
        
        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                img_url = urljoin(base_url, src)
                # Prefer images from img*.irna.ir
                if 'irna.ir' in img_url.lower() and not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'thumb', 'thumbnail', 'small', 'ad']):
                    return img_url
                # Check image size if available
                if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'thumb', 'thumbnail', 'small', 'ad']):
                    width = img.get('width', '')
                    height = img.get('height', '')
                    if width and height:
                        try:
                            w, h = int(width), int(height)
                            if w > 200 and h > 200:
                                return img_url
                        except:
                            pass
                    else:
                        # If URL looks like IRNA CDN image, use it
                        if 'irna.ir' in img_url.lower() and 'img' in img_url.lower():
                            return img_url
        
        return None

