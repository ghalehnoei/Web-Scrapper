"""ISNA (Islamic Students' News Agency) listing page source implementation."""

import asyncio
import hashlib
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from sources.base import BaseRSSSource


# Global logger - will be set by main module
logger = None


def set_logger(logger_instance):
    """Set the logger instance for this module."""
    global logger
    logger = logger_instance


class ISNASource(BaseRSSSource):
    """ISNA (Islamic Students' News Agency) listing page source implementation."""
    
    def __init__(self, listing_url: str, timeout: float, retry_count: int, retry_delay: float):
        self._listing_url = listing_url
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
    
    @property
    def source_name(self) -> str:
        return 'isna'
    
    @property
    def rss_url(self) -> str:
        # For compatibility with BaseRSSSource interface
        return self._listing_url
    
    def extract_news_code(self, url: str) -> str:
        """Extract news code from ISNA URL."""
        # ISNA URLs typically have structure like:
        # https://www.isna.ir/news/14041015-12345/...
        # Pattern: /news/{date}-{numeric_id}/{slug}
        # Or: https://www.isna.ir/news/12345678/...
        # Pattern: /news/{numeric_id}/{slug}
        
        # Try to extract numeric ID from /news/{id}/ or /news/{date}-{id}/ pattern
        match = re.search(r'/news/(?:\d{8}-)?(\d+)', url)
        if match:
            return match.group(1)
        
        # Fallback: use hash of URL
        return hashlib.md5(url.encode()).hexdigest()[:8]
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch listing page and extract article URLs from ISNA archive."""
        for attempt in range(self.retry_count):
            try:
                response = await client.get(self._listing_url, timeout=self.timeout)
                response.raise_for_status()
                html = response.text
                
                if logger:
                    logger.info(f"Fetched listing page, HTML length: {len(html)}", url=self._listing_url, source_name=self.source_name)
                
                # Extract article URLs from the listing page
                article_urls = self.extract_article_urls(html, self._listing_url)
                
                if not article_urls:
                    if logger:
                        logger.warning(f"No article URLs found on listing page", url=self._listing_url, source_name=self.source_name)
                    if attempt < self.retry_count - 1:
                        await asyncio.sleep(self.retry_delay)
                        continue
                    return []
                
                # Convert URLs to RSS-like items
                items = []
                for url in article_urls:
                    items.append({
                        'title': '',  # Will be extracted from article page
                        'link': url,
                        'guid': url,
                        'pubDate': '',  # Will be extracted from article page if available
                        'description': '',
                        'category': ''
                    })
                
                if logger:
                    logger.info(f"Extracted {len(items)} article URLs from listing page", url=self._listing_url, source_name=self.source_name)
                
                return items
            except Exception as e:
                if attempt < self.retry_count - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                if logger:
                    logger.error(f"Error fetching listing page after {self.retry_count} attempts", url=self._listing_url, source_name=self.source_name, error=str(e))
                return []
    
    def extract_article_urls(self, html: str, base_url: str) -> List[str]:
        """Extract article URLs from ISNA listing page."""
        soup = BeautifulSoup(html, 'html.parser')
        base_domain = urlparse(base_url).netloc
        found_links = set()
        
        # Strategy 1: Find links in article containers or news items
        # ISNA typically uses specific classes for article links
        article_selectors = [
            'a[href*="/news/"]',
            'article a[href*="/news/"]',
            '.news-item a',
            '.article-item a',
            '.story-item a',
            '[class*="news"] a[href*="/news/"]',
            '[class*="article"] a[href*="/news/"]',
            '.archive-item a',
            '[class*="archive"] a[href*="/news/"]',
        ]
        
        for selector in article_selectors:
            links = soup.select(selector)
            for link in links:
                href = link.get('href', '')
                if not href:
                    continue
                
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                
                # Check domain match
                if parsed.netloc and parsed.netloc != base_domain:
                    continue
                
                # Check if URL matches ISNA article pattern
                if self._is_valid_news_url(parsed.path):
                    found_links.add(self._normalize_url(full_url))
        
        # Strategy 2: Find all links with /news/ pattern
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            if not href or '/news/' not in href:
                continue
            
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            
            # Check domain match
            if parsed.netloc and parsed.netloc != base_domain:
                continue
            
            # Check if URL matches article pattern
            if self._is_valid_news_url(parsed.path):
                found_links.add(self._normalize_url(full_url))
        
        if logger:
            logger.info(f"Found {len(found_links)} potential article URLs from listing page", source_name=self.source_name, url_count=len(found_links))
            if len(found_links) > 0:
                sample_urls = list(found_links)[:5]
                logger.info(f"Sample URLs found: {sample_urls}", source_name=self.source_name)
        
        return list(found_links)
    
    def _is_valid_news_url(self, path: str) -> bool:
        """Check if a URL path matches ISNA article patterns."""
        # ISNA URLs have patterns:
        # /news/{date}-{id}/{slug} - e.g., /news/14041015-12345/slug
        # /news/{id}/{slug} - e.g., /news/12345678/slug
        
        if '/news/' not in path.lower():
            return False
        
        # Exclude old archived news URLs (e.g., /news/95031711058, /news/95031711051, /news/95031711050)
        # These appear to be old archived items with date prefix "950317"
        if '/news/950317' in path:
            return False
        
        # Extract parts after /news/
        parts = path.split('/')
        try:
            news_idx = [i for i, p in enumerate(parts) if p.lower() == 'news']
            if not news_idx:
                return False
            
            for n_idx in news_idx:
                if n_idx + 1 < len(parts):
                    news_segment = parts[n_idx + 1]
                    # Exclude specific old archived news IDs
                    if news_segment in ['95031711058', '95031711051', '95031711050', '1402052314622']:
                        return False
                    # Check if it matches date-id pattern (14041015-12345) or just numeric ID
                    if re.match(r'^\d{8}-\d+$', news_segment):
                        return True  # Date-ID pattern
                    if re.match(r'^\d{6,}$', news_segment):
                        return True  # Numeric ID pattern (6+ digits)
        except:
            pass
        
        return False
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL by removing trailing slash, fragments, and query params."""
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip('/')
        return f"{parsed.scheme}://{parsed.netloc}{clean_path}"
    
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Extract article content from ISNA HTML page."""
        for attempt in range(self.retry_count):
            try:
                response = await client.get(url, follow_redirects=True, timeout=self.timeout)
                response.raise_for_status()
                
                html = response.text
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
                    '.story-body',
                    '.news-content'
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
        """Extract main image URL from ISNA HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Strategy 1: Try og:image meta tag
        og_image = soup.find('meta', property='og:image')
        if og_image:
            img_url = og_image.get('content', '')
            if img_url:
                full_url = urljoin(base_url, img_url)
                if not any(skip in full_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
                    return full_url
        
        # Strategy 2: Try ISNA-specific image selectors
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
                    # Prefer images from isna.ir domain
                    if 'isna.ir' in img_url.lower() and not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'ad']):
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
                # Prefer images from isna.ir
                if 'isna.ir' in img_url.lower() and not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'thumb', 'thumbnail', 'small', 'ad']):
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
                        # If URL looks like ISNA image, use it
                        if 'isna.ir' in img_url.lower() and 'img' in img_url.lower():
                            return img_url
        
        return None

