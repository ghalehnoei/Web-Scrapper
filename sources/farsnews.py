"""Fars News Agency listing page source implementation (non-RSS)."""

import asyncio
import hashlib
import json
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from sources.base import BaseRSSSource


# Global logger - will be set by main module
logger = None


def set_logger(logger_instance):
    """Set the logger instance for this module."""
    global logger
    logger = logger_instance


class FarsSource(BaseRSSSource):
    """Fars News Agency listing page source implementation (non-RSS)."""
    
    def __init__(self, listing_url: str, timeout: float, retry_count: int, retry_delay: float):
        self._listing_url = listing_url
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self._playwright = None
        self._browser = None
        self._browser_context = None
    
    @property
    def source_name(self) -> str:
        return 'farsnews'
    
    @property
    def rss_url(self) -> str:
        """Return listing page URL (for compatibility with existing engine)."""
        return self._listing_url
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch listing page and extract article URLs (returns items in RSS-like format).

        For Fars News, the listing (including `https://farsnews.ir/search?topicID=0`)
        is a JavaScript-rendered SPA, so we try to use the Playwright headless
        browser first, then fall back to plain HTTP if needed.
        """
        for attempt in range(self.retry_count):
            try:
                html = ''

                # Try to use headless browser for JS-rendered listing
                browser_context, playwright = await self._get_browser_context()
                if browser_context:
                    page = await browser_context.new_page()
                    try:
                        if logger:
                            logger.info("Using headless browser to fetch listing page", url=self._listing_url, source_name=self.source_name)
                        await page.goto(self._listing_url, wait_until='networkidle', timeout=int(self.timeout * 1000))
                        # Wait a bit for search results to render
                        await page.wait_for_timeout(3000)
                        # Try to wait for result/title elements to appear
                        try:
                            await page.wait_for_selector('a[href*="/"], .text-title1-b, [class*="text-title1-b"]', timeout=5000)
                        except:
                            pass
                        html = await page.content()
                        await page.close()
                    except Exception as browser_error:
                        try:
                            await page.close()
                        except:
                            pass
                        if logger:
                            logger.warning(
                                f"Headless browser failed for listing page, attempt {attempt + 1}/{self.retry_count}: {str(browser_error)}",
                                url=self._listing_url,
                                source_name=self.source_name,
                            )
                        html = ''

                # Fallback to HTTP if browser not available or failed
                if not html:
                    if logger:
                        logger.info("Falling back to HTTP client for listing page", url=self._listing_url, source_name=self.source_name)
                    response = await client.get(self._listing_url, timeout=self.timeout)
                    response.raise_for_status()
                    html = response.text

                # Log page size for debugging
                if logger:
                    logger.info(f"Fetched listing page, size: {len(html)} bytes", url=self._listing_url, source_name=self.source_name)

                article_urls = self.extract_article_urls(html, self._listing_url)

                if not article_urls and logger:
                    # If no URLs found, log a warning with some debug info
                    soup = BeautifulSoup(html, 'html.parser')
                    all_links = soup.find_all('a', href=True)
                    logger.warning(
                        f"No article URLs found on listing page. Total links on page: {len(all_links)}",
                        url=self._listing_url,
                        source_name=self.source_name,
                    )
                    # Log first few links as sample
                    if all_links:
                        sample_hrefs = [link.get('href', '')[:100] for link in all_links[:10]]
                        logger.warning(f"Sample hrefs found: {sample_hrefs}", source_name=self.source_name)

                # Deduplicate URLs within the page
                unique_urls = list(dict.fromkeys(article_urls))  # Preserves order

                # Convert URLs to RSS-like items format
                items = []
                for url in unique_urls:
                    items.append({
                        'title': '',  # Will be extracted from article page
                        'link': url,
                        'guid': url,  # Use URL as GUID
                        'pubDate': '',  # Will be extracted if available from article
                        'description': '',
                        'category': ''
                    })

                return items
            except Exception as e:
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                if logger:
                    logger.error(
                        f"Error fetching listing page after {self.retry_count} attempts",
                        url=self._listing_url,
                        source_name=self.source_name,
                        error=str(e),
                    )
                return []
    
    def extract_article_urls(self, html: str, base_url: str) -> List[str]:
        """Extract article URLs from Fars News listing page."""
        soup = BeautifulSoup(html, 'html.parser')
        
        article_urls = []
        base_domain = urlparse(base_url).netloc
        
        found_links = set()
        
        # Strategy 1: Find links that contain or are near elements with the title class
        # Title class: "text-title1-b line-clamp-3 whitespace-pre-wrap prosed mb-1"
        # Note: "@" symbol might be part of class or a typo, so we check for both
        # Look for elements with this class and find their parent/sibling links
        title_selectors = [
            '.text-title1-b',
            '[class*="text-title1-b"]',
            '[class*="line-clamp-3"]',
            '[class*="@text-title1-b"]',  # Handle @ symbol if it's part of class
            'h1', 'h2', 'h3', 'h4',  # Common title tags
        ]
        
        for selector in title_selectors:
            title_elements = soup.select(selector)
            for title_elem in title_elements:
                # Check if the title element itself is a link
                if title_elem.name == 'a' and title_elem.get('href'):
                    href = title_elem.get('href')
                    full_url = urljoin(base_url, href)
                    parsed = urlparse(full_url)
                    if parsed.netloc == base_domain or not parsed.netloc:
                        if self._is_valid_news_url(parsed.path):
                            found_links.add(self._normalize_url(full_url))
                
                # Check if title is inside a link
                parent_link = title_elem.find_parent('a', href=True)
                if parent_link:
                    href = parent_link.get('href')
                    full_url = urljoin(base_url, href)
                    parsed = urlparse(full_url)
                    if parsed.netloc == base_domain or not parsed.netloc:
                        if self._is_valid_news_url(parsed.path):
                            found_links.add(self._normalize_url(full_url))
                
                # Check for sibling links
                for sibling in title_elem.find_next_siblings('a', href=True, limit=2):
                    href = sibling.get('href')
                    full_url = urljoin(base_url, href)
                    parsed = urlparse(full_url)
                    if parsed.netloc == base_domain or not parsed.netloc:
                        if self._is_valid_news_url(parsed.path):
                            found_links.add(self._normalize_url(full_url))
        
        # Strategy 2: Find all links with href containing /news/
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Resolve relative URLs
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Skip topic/hashtag pages entirely (not real news items)
            path_lower = parsed.path.lower()
            if any(seg in path_lower for seg in ['/topic/', '/topics/', '/hashtag/', '/hash-tag/']):
                continue
            
            # Check domain match first
            if parsed.netloc and parsed.netloc != base_domain:
                continue
            
            # Check if URL matches article patterns
            if not self._is_valid_news_url(parsed.path):
                continue
            
            # Ignore videos, galleries, live streams, ads, external links
            # But allow category/author paths (they're part of the URL structure like /omolbanin_khosravi/...)
            # Only exclude if these are separate path segments, not part of the category name
            if any(skip in path_lower for skip in ['/video/', '/gallery/', '/live/', '/ad/', '/ads/', '/tag/', '/category/', '/author/']):
                continue
            
            found_links.add(self._normalize_url(full_url))
        
        # Log what we found for debugging
        if logger:
            logger.info(f"Found {len(found_links)} potential article URLs from listing page", source_name=self.source_name, url_count=len(found_links))
            if len(found_links) > 0:
                # Log first few URLs as sample
                sample_urls = list(found_links)[:5]
                logger.info(f"Sample URLs found: {sample_urls}", source_name=self.source_name)
        
        article_urls = list(found_links)
        return article_urls
    
    def _is_valid_news_url(self, path: str) -> bool:
        """Check if a URL path matches Fars News article patterns."""
        path_lower = path.lower()

        # Explicitly ignore topic/hashtag paths (these are index pages, not news articles)
        if any(seg in path_lower for seg in ['/topic/', '/topics/', '/hashtag/', '/hash-tag/']):
            return False

        # Fars News URLs have pattern: /{category}/{long_numeric_id}/{slug}
        # Example: /omolbanin_khosravi/1765950737982829978/slug-text
        # The numeric ID is typically very long (15+ digits)
        
        # Pattern: /something/numeric_id/something
        # Match paths with at least 2 segments where the second segment is a long number
        path_parts = [p for p in path.split('/') if p]  # Remove empty parts
        
        if len(path_parts) >= 2:
            # Check if second segment is a long numeric ID (10+ digits)
            second_segment = path_parts[1]
            if re.match(r'^\d{10,}$', second_segment):
                return True
        
        # Also check for /news/ patterns (in case some articles use that)
        if '/news/' in path.lower():
            news_patterns = [
                r'/news/\d+',  # /news/123456
                r'/fa/news/\d+',  # /fa/news/123456
                r'/news/\d+/\d+/\d+/\d+',  # /news/1404/01/15/1234567 (date-based)
                r'/fa/news/\d+/\d+/\d+/\d+',  # /fa/news/1404/01/15/1234567
            ]
            
            for pattern in news_patterns:
                if re.search(pattern, path):
                    return True
        
        return False
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL by removing trailing slash, fragments, and query params."""
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip('/')
        return f"{parsed.scheme}://{parsed.netloc}{clean_path}"
    
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
                    logger.info("Initializing Playwright browser...", source_name=self.source_name)
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
                    logger.info("Playwright browser initialized successfully", source_name=self.source_name)
            
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
        """Extract article content from Fars News HTML page using headless browser."""
        page = None
        for attempt in range(self.retry_count):
            try:
                # Use headless browser for JavaScript-rendered content
                browser_context, playwright = await self._get_browser_context()
                if browser_context:
                    page = await browser_context.new_page()
                    try:
                        if logger:
                            logger.info(f"Using headless browser to fetch article", url=url, source_name=self.source_name)
                        await page.goto(url, wait_until='networkidle', timeout=int(self.timeout * 1000))
                        # Wait for content to load (JavaScript rendering)
                        await page.wait_for_timeout(3000)  # Wait 3 seconds for JS to render
                        # Try to wait for article content to appear
                        try:
                            await page.wait_for_selector('article, .prose, .news-body, [class*="content"], p', timeout=5000)
                        except:
                            pass  # Continue even if selector not found

                        # Some Fars pages lazy‑render additional paragraphs on scroll.
                        # Scroll down the page a few times to trigger any lazy loading/virtualization.
                        try:
                            for _ in range(5):
                                await page.evaluate("window.scrollBy(0, document.body.scrollHeight / 5);")
                                await page.wait_for_timeout(500)
                        except Exception:
                            # Scrolling is a best‑effort enhancement; ignore failures.
                            pass
                        html = await page.content()
                        await page.close()
                        page = None
                        if logger:
                            logger.info(f"Successfully fetched page with headless browser, HTML length: {len(html)}", url=url, source_name=self.source_name)
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
                
                # Check if page requires JavaScript (common with modern SPAs)
                js_required_indicators = [
                    'Enable JavaScript to use this web application',
                    'فعال بودن جاوااسکریپت الزامی است',
                    'لطفا جاوااسکریپت را فعال کنید',
                    'JavaScript is required',
                    'noscript'
                ]
                
                requires_js = any(indicator in html for indicator in js_required_indicators)
                if requires_js and logger:
                    logger.warning(f"Page appears to require JavaScript. Attempting to extract content anyway...", url=url, source_name=self.source_name)
                
                main_image_url = self.extract_main_image(html, url)
                
                soup = BeautifulSoup(html, 'html.parser')
                
                title = ''
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text(strip=True)
                
                # Also try to get title from meta tags
                if not title:
                    og_title = soup.find('meta', property='og:title')
                    if og_title:
                        title = og_title.get('content', '')
                
                # Extract body - try multiple strategies for Fars News
                body = ''

                # Strategy 1 (primary): Fars News inner prose container (high priority)
                # On Fars, the actual article body is often inside a div like: <div class="n-c4q897 prosed">...</div>
                if not body or len(body.strip()) < 50:
                    # Collect all relevant content containers: main prose blocks can be split
                    prose_containers = soup.select('div.n-c4q897.prosed, div.prosed.n-c4q897, div.prosed')
                    if prose_containers:
                        body_parts = []
                        for prose_container in prose_containers:
                            # Clean inner container: remove scripts, ads, galleries, etc.
                            for unwanted in prose_container(['script', 'style', 'noscript', 'iframe', 'aside', 'nav', 'header', 'footer', 'form']):
                                unwanted.decompose()
                            for ad in prose_container.select(
                                '.ad, .advertisement, .ads, [class*="ad-"], [class*="advertisement"], [rel="sponsored"], a[rel~="sponsored"]'
                            ):
                                ad.decompose()
                            
                            # Strategy 1a: Collect ALL span.auto-dir-block elements inside this container
                            span_elems = prose_container.select('span.auto-dir-block')
                            if span_elems:
                                for span in span_elems:
                                    span_html = span.decode_contents().strip()
                                    if not span_html or len(span_html) < 10:
                                        continue
                                    if span_html.strip() in ['', ' ', '\n', '\t']:
                                        continue
                                    body_parts.append(f'<p dir="rtl">{span_html}</p>')
                        
                        # If we still have too few paragraphs, also scan <p> tags inside all prose containers
                        if not body_parts or len(body_parts) < 2:
                            for prose_container in prose_containers:
                                p_elems = prose_container.find_all('p', recursive=True)
                                for p in p_elems:
                                    p_html = p.decode_contents().strip()
                                    if not p_html or len(p_html) < 10:
                                        continue
                                    is_duplicate = False
                                    for existing_part in body_parts:
                                        if p_html in existing_part or existing_part in p_html:
                                            is_duplicate = True
                                            break
                                    if not is_duplicate:
                                        body_parts.append(f'<p dir="rtl">{p_html}</p>')

                        # Fallback: if no structured elements, use raw inner HTML from all containers
                        if not body_parts:
                            combined_html = []
                            for prose_container in prose_containers:
                                combined_html.append(prose_container.decode_contents())
                            body = '\n'.join(combined_html)
                        else:
                            body = '\n'.join(body_parts)
                        
                        if body and len(body.strip()) > 50:
                            if logger:
                                logger.info(
                                    f"Extracted body from Fars prose container: {len(body_parts)} paragraphs, total length: {len(body)}",
                                    url=url,
                                    source_name=self.source_name,
                                    paragraph_count=len(body_parts)
                                )
                
                # Strategy 2: Try to find content in script tags (JSON-LD, initial state, etc.)
                # Some SPAs embed content in script tags as JSON
                if not body or len(body.strip()) < 100:
                    script_tags = soup.find_all('script', type='application/ld+json')
                    for script in script_tags:
                        try:
                            import json
                            data = json.loads(script.string)
                            if isinstance(data, dict):
                                # Try to extract articleBody from JSON-LD
                                if 'articleBody' in data:
                                    body = data['articleBody']
                                    if logger:
                                        logger.info(f"Extracted body from JSON-LD, length: {len(body)}", url=url, source_name=self.source_name)
                                    break
                        except:
                            continue
                    
                    # Also check for initial state or data in script tags
                    if not body or len(body.strip()) < 100:
                        all_scripts = soup.find_all('script')
                        for script in all_scripts:
                            if script.string and len(script.string) > 500:
                                # Look for article content in script
                                script_text = script.string
                                # Try to find articleBody or content patterns
                                if 'articleBody' in script_text or 'content' in script_text.lower():
                                    # Try to extract HTML from script
                                    if '<p>' in script_text or '<div' in script_text:
                                        # Extract HTML content from script
                                        script_soup = BeautifulSoup(script_text, 'html.parser')
                                        paragraphs = script_soup.find_all('p')
                                        if len(paragraphs) >= 2:
                                            body_parts = [p.decode_contents() for p in paragraphs]
                                            body = '\n'.join(body_parts)
                                            if body and len(body.strip()) > 100:
                                                if logger:
                                                    logger.info(f"Extracted body from script tag, length: {len(body)}", url=url, source_name=self.source_name)
                                                break
                
                # Strategy 3: Try article tag
                if not body or len(body.strip()) < 100:
                    article_elem = soup.find('article')
                    if article_elem:
                        for script in article_elem(['script', 'style', 'noscript']):
                            script.decompose()
                        body = article_elem.decode_contents()
                        if body and len(body.strip()) > 100:
                            if logger:
                                logger.info(f"Extracted body from article tag, length: {len(body)}", url=url, source_name=self.source_name)
                
                # Strategy 4: Try Fars News specific selectors (fallbacks)
                if not body or len(body.strip()) < 100:
                    content_selectors = [
                        '.news-body',
                        '.nt-body',
                        '.news-content',
                        '.article-content',
                        '.story-body',
                        '.content-body',
                        # Fars News often uses Tailwind/Prose style content containers
                        '.prose',
                        '.prosed',
                        '[class*="prose"]',
                        '[class*="prosed"]',
                        # Generic fallbacks
                        '[class*="news-body"]',
                        '[class*="content"]',
                        '[class*="article"]',
                        '[itemprop="articleBody"]',
                        'main .content',
                        'main article',
                        '.post-content',
                        '.entry-content',
                        '#content',
                        '#main-content',
                        '.main-content'
                    ]
                    
                    for selector in content_selectors:
                        content_elem = soup.select_one(selector)
                        if content_elem:
                            # Remove unwanted elements
                            for unwanted in content_elem(['script', 'style', 'noscript', 'iframe', 'aside', 'nav', 'header', 'footer', 'form']):
                                unwanted.decompose()
                            # Remove ads and related content
                            for ad in content_elem.select('.ad, .advertisement, .ads, [class*="ad-"], [class*="advertisement"], [rel="sponsored"], a[rel~="sponsored"]'):
                                ad.decompose()
                            
                            body = content_elem.decode_contents()
                            if body and len(body.strip()) > 100:
                                if logger:
                                    logger.info(f"Extracted body from selector '{selector}', length: {len(body)}", url=url, source_name=self.source_name)
                                break
                
                # Strategy 5: Find main content area
                if not body or len(body.strip()) < 100:
                    main_elem = soup.find('main')
                    if main_elem:
                        # Remove unwanted elements
                        for unwanted in main_elem(['script', 'style', 'noscript', 'iframe', 'aside', 'nav', 'header', 'footer']):
                            unwanted.decompose()
                        for ad in main_elem.select('.ad, .advertisement, .ads, [class*="ad-"]'):
                            ad.decompose()
                        
                        # Try to find the main content div within main
                        content_div = main_elem.select_one('.content, .article, [class*="content"], [class*="article"]')
                        if content_div:
                            body = content_div.decode_contents()
                        else:
                            body = main_elem.decode_contents()
                        
                        if body and len(body.strip()) > 100:
                            if logger:
                                logger.info(f"Extracted body from main tag, length: {len(body)}", url=url, source_name=self.source_name)
                
                # Strategy 6: Find divs with substantial paragraph content
                if not body or len(body.strip()) < 100:
                    # Look for divs with lots of paragraph tags
                    all_divs = soup.find_all('div')
                    for div in all_divs:
                        paragraphs = div.find_all('p')
                        if len(paragraphs) >= 2:  # At least 2 paragraphs
                            # Check if it has substantial text
                            text_content = div.get_text(strip=True)
                            if len(text_content) > 300:  # Substantial content
                                # Skip if it's likely navigation or header
                                div_classes = ' '.join(div.get('class', [])).lower()
                                if any(skip in div_classes for skip in ['nav', 'header', 'footer', 'menu', 'sidebar', 'ad']):
                                    continue
                                
                                for unwanted in div(['script', 'style', 'noscript', 'iframe', 'aside', 'nav', 'header', 'footer', 'form']):
                                    unwanted.decompose()
                                body = div.decode_contents()
                                if body and len(body.strip()) > 100:
                                    if logger:
                                        logger.info(f"Extracted body from div with {len(paragraphs)} paragraphs, length: {len(body)}", url=url, source_name=self.source_name)
                                    break
                
                # Strategy 7: Collect all paragraphs and reconstruct body
                if not body or len(body.strip()) < 100:
                    # Find all paragraph elements that are likely article content
                    all_paragraphs = soup.find_all('p')
                    if len(all_paragraphs) >= 2:
                        # Filter paragraphs that are likely content (not navigation, etc.)
                        content_paragraphs = []
                        for p in all_paragraphs:
                            p_text = p.get_text(strip=True)
                            # Skip very short paragraphs (likely navigation)
                            if len(p_text) < 20:
                                continue
                            # Skip if parent is nav, header, footer
                            parent = p.find_parent()
                            if parent:
                                parent_classes = ' '.join(parent.get('class', [])).lower()
                                if any(skip in parent_classes for skip in ['nav', 'header', 'footer', 'menu', 'sidebar', 'ad']):
                                    continue
                            content_paragraphs.append(p)
                        
                        if len(content_paragraphs) >= 2:
                            # Reconstruct body from paragraphs
                            body_parts = []
                            for p in content_paragraphs:
                                # Remove scripts/styles from paragraph
                                for unwanted in p(['script', 'style', 'noscript']):
                                    unwanted.decompose()
                                body_parts.append(p.decode_contents())
                            body = '\n'.join(body_parts)
                            if body and len(body.strip()) > 100:
                                if logger:
                                    logger.info(f"Extracted body from {len(content_paragraphs)} paragraphs, length: {len(body)}", url=url, source_name=self.source_name)
                
                # Log warning if body is still empty or too short
                if not body or len(body.strip()) < 50:
                    if logger:
                        # Log some debug info
                        all_p_tags = soup.find_all('p')
                        all_divs = soup.find_all('div')
                        logger.warning(f"Could not extract substantial body content. Found {len(all_p_tags)} <p> tags, {len(all_divs)} <div> tags. Body length: {len(body) if body else 0}", url=url, source_name=self.source_name)
                        # Log page structure for debugging
                        main_tag = soup.find('main')
                        article_tag = soup.find('article')
                        body_tag = soup.find('body')
                        logger.warning(f"Page structure - main: {main_tag is not None}, article: {article_tag is not None}, body: {body_tag is not None}", url=url, source_name=self.source_name)
                        
                        # Log sample of page HTML structure (first 500 chars)
                        if body_tag:
                            sample_html = str(body_tag)[:500]
                            logger.warning(f"Sample HTML structure: {sample_html}", url=url, source_name=self.source_name)
                
                # Filter out JavaScript requirement messages from body
                if body:
                    js_message_patterns = [
                        'Enable JavaScript to use this web application',
                        'فعال بودن جاوااسکریپت الزامی است',
                        'لطفا جاوااسکریپت را فعال کنید',
                        'JavaScript is required',
                    ]
                    body_text_lower = body.lower()
                    if any(pattern.lower() in body_text_lower for pattern in js_message_patterns):
                        # If body only contains JS requirement message, clear it
                        body_lines = body.split('\n')
                        filtered_lines = [line for line in body_lines if not any(pattern.lower() in line.lower() for pattern in js_message_patterns)]
                        body = '\n'.join(filtered_lines).strip()
                        if len(body) < 50:  # If only JS message was removed, body is too short
                            body = ''
                            if logger:
                                logger.warning(f"Body contained only JavaScript requirement message, cleared body", url=url, source_name=self.source_name)
                
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
        """Extract main image URL from Fars News HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Strategy 1: Prefer og:image meta tag
        og_image = soup.find('meta', property='og:image')
        if og_image:
            img_url = og_image.get('content', '')
            if img_url:
                full_url = urljoin(base_url, img_url)
                if not any(skip in full_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
                    return full_url
        
        # Strategy 2: Fars News specific image selectors
        # Fars News often uses images in specific containers or with specific classes
        fars_image_selectors = [
            # Look for images in the prose container area (near article content)
            'div.prosed img',
            'div.n-c4q897.prosed img',
            # Look for images in article header/lead area
            'article header img',
            '.article-header img',
            '.news-header img',
            'header .lead-image img',
            '.lead-image img',
            # Look for images in gallery/featured image containers
            '.featured-image img',
            '.article-image img',
            '.main-image img',
            '[class*="featured"] img',
            '[class*="article-image"] img',
            '[class*="main-image"] img',
            # Look for images with specific Fars News classes
            'img[class*="n-h3v2f0"]',  # Common Fars News image class
            'img[class*="rounded-3"]',  # Fars News often uses rounded images
        ]
        
        for selector in fars_image_selectors:
            img_elem = soup.select_one(selector)
            if img_elem:
                src = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src') or img_elem.get('data-original')
                if src:
                    img_url = urljoin(base_url, src)
                    # Skip logos, icons, avatars, ads, thumbnails
                    if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad', 'thumb', 'thumbnail', 'spinner']):
                        # Prefer images from cdn.farsnews.ir
                        if 'cdn.farsnews.ir' in img_url.lower():
                            return img_url
                        # Also accept other domains if they look like article images
                        if not any(skip in img_url.lower() for skip in ['social', 'share', 'button']):
                            return img_url
        
        # Strategy 3: Find images in the main prose container (where article content is)
        prose_container = soup.select_one('div.n-c4q897.prosed') or soup.select_one('div.prosed.n-c4q897')
        if not prose_container:
            # Try to find any prose container
            prose_containers = soup.select('div.prosed')
            if prose_containers:
                # Find the one with the most content
                best_container = max(prose_containers, key=lambda d: len(d.get_text(strip=True)))
                prose_container = best_container
        
        if prose_container:
            # Look for images near the beginning of the prose container
            # (main article images are usually at the top)
            img_tags = prose_container.find_all('img', limit=10)
            for img in img_tags:
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
                if src:
                    img_url = urljoin(base_url, src)
                    # Skip logos, icons, avatars, ads, thumbnails
                    if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad', 'thumb', 'thumbnail', 'spinner', 'social', 'share']):
                        # Prefer images from cdn.farsnews.ir
                        if 'cdn.farsnews.ir' in img_url.lower():
                            return img_url
                        # Check image size if available
                        width = img.get('width', '')
                        height = img.get('height', '')
                        if width and height:
                            try:
                                w, h = int(width), int(height)
                                if w > 200 and h > 200:
                                    return img_url
                            except:
                                pass
                        # If no size info but URL looks good, use it
                        if 'cdn.farsnews.ir' in img_url.lower() or ('farsnews' in img_url.lower() and 'cdn' in img_url.lower()):
                            return img_url
        
        # Strategy 4: Fallback - find first large image in article or content area
        article = soup.find('article')
        if article:
            img_tags = article.find_all('img', limit=10)
        else:
            content_elem = soup.select_one('.news-body, .nt-body, .content, main')
            if content_elem:
                img_tags = content_elem.find_all('img', limit=10)
            else:
                img_tags = []
        
        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
            if src:
                img_url = urljoin(base_url, src)
                # Skip logos, icons, avatars, ads, thumbnails
                if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad', 'thumb', 'thumbnail', 'spinner', 'social', 'share']):
                    # Prefer images from cdn.farsnews.ir
                    if 'cdn.farsnews.ir' in img_url.lower():
                        return img_url
                    # Check image size if available
                    width = img.get('width', '')
                    height = img.get('height', '')
                    if width and height:
                        try:
                            w, h = int(width), int(height)
                            if w > 200 and h > 200:
                                return img_url
                        except:
                            pass
                    # If URL looks like a Fars News CDN image, use it
                    if 'cdn.farsnews.ir' in img_url.lower() or ('farsnews' in img_url.lower() and 'cdn' in img_url.lower()):
                        return img_url
        
        return None
    
    def extract_news_code(self, url: str) -> str:
        """Extract news code from Fars News URL."""
        # Fars URLs have pattern: /{category}/{long_numeric_id}/{slug}
        # Example: /omolbanin_khosravi/1765950737982829978/slug-text
        # The numeric ID is the second path segment and is typically very long (15+ digits)
        
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]  # Remove empty parts
        
        if len(path_parts) >= 2:
            # Second segment should be the numeric ID
            second_segment = path_parts[1]
            if re.match(r'^\d{10,}$', second_segment):
                return second_segment
        
        # Fallback: Try to extract long numeric segments from URL
        # Look for very long numbers (15+ digits) which are likely article IDs
        matches = re.findall(r'/(\d+)', url)
        if matches:
            # Find the longest numeric segment (likely the article ID)
            longest = max(matches, key=len)
            if len(longest) >= 10:  # Fars News IDs are typically very long
                return longest
            # If no long segment, use the longest one anyway
            return longest
        
        # Final fallback: use hash of URL
        return hashlib.md5(url.encode()).hexdigest()[:8]

