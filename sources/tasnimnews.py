"""Tasnim News Agency listing page source implementation."""

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


class TasnimSource(BaseRSSSource):
    """Tasnim News Agency listing page source implementation."""
    
    def __init__(self, listing_url: str, timeout: float, retry_count: int, retry_delay: float):
        self._listing_url = listing_url
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
    
    @property
    def source_name(self) -> str:
        return 'tasnimnews'
    
    @property
    def rss_url(self) -> str:
        # For compatibility with BaseRSSSource interface
        return self._listing_url
    
    def extract_news_code(self, url: str) -> str:
        """Extract news code from Tasnim News URL."""
        # Tasnim URLs typically have structure like:
        # https://www.tasnimnews.com/fa/news/1404/01/15/1234567/...
        # Pattern: /year(4 digits)/month(2 digits)/day(2 digits)/article_id(6+ digits)
        # We need to extract the article ID (last numeric segment, usually 6+ digits)
        
        # Extract all numeric segments from URL path
        segments = re.findall(r'/(\d+)', url)
        if segments:
            # The article ID is typically the last segment and is usually 6+ digits
            # Year is 4 digits (1404), month/day are 2 digits, article ID is longer
            for segment in reversed(segments):
                # Article ID is usually 6 or more digits
                if len(segment) >= 6:
                    return segment
            # If no long segment found, use the last segment anyway
            return segments[-1]
        
        # Fallback: use hash of URL
        return hashlib.md5(url.encode()).hexdigest()[:8]
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch listing page and extract article URLs from Tasnim News archive."""
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
        """Extract article URLs from Tasnim News listing page."""
        soup = BeautifulSoup(html, 'html.parser')
        base_domain = urlparse(base_url).netloc
        found_links = set()
        
        # Strategy 1: Find links in article containers or news items
        # Tasnim News typically uses specific classes for article links
        article_selectors = [
            'a[href*="/fa/news/"]',
            'article a[href*="/fa/news/"]',
            '.news-item a',
            '.article-item a',
            '.story-item a',
            '[class*="news"] a[href*="/fa/news/"]',
            '[class*="article"] a[href*="/fa/news/"]',
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
                
                # Check if URL matches Tasnim News article pattern
                if self._is_valid_news_url(parsed.path):
                    found_links.add(self._normalize_url(full_url))
        
        # Strategy 2: Find all links with /fa/news/ pattern
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            if not href or '/fa/news/' not in href:
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
        """Check if a URL path matches Tasnim News article patterns."""
        # Tasnim URLs have pattern: /fa/news/year/month/day/article_id/slug
        # Example: /fa/news/1404/01/15/1234567/slug-text
        # Pattern: /fa/news/4digits/2digits/2digits/6+digits
        
        if '/fa/news/' not in path.lower():
            return False
        
        # Extract numeric segments after /fa/news/
        parts = path.split('/')
        try:
            fa_idx = [i for i, p in enumerate(parts) if p.lower() == 'fa']
            news_idx = [i for i, p in enumerate(parts) if p.lower() == 'news']
            
            if not fa_idx or not news_idx:
                return False
            
            # Check if 'fa' and 'news' are consecutive
            for f_idx in fa_idx:
                for n_idx in news_idx:
                    if n_idx == f_idx + 1:
                        # After /fa/news/, we should have: year/month/day/article_id
                        after_news = parts[n_idx + 1:]
                        if len(after_news) >= 4:
                            # Check if we have numeric segments
                            year = after_news[0]
                            month = after_news[1] if len(after_news) > 1 else ''
                            day = after_news[2] if len(after_news) > 2 else ''
                            article_id = after_news[3] if len(after_news) > 3 else ''
                            
                            # Year should be 4 digits (1404, etc.), month/day 2 digits, article_id 6+ digits
                            if (re.match(r'^\d{4}$', year) and 
                                re.match(r'^\d{2}$', month) and 
                                re.match(r'^\d{2}$', day) and 
                                re.match(r'^\d{6,}$', article_id)):
                                return True
        except:
            pass
        
        return False
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL by removing trailing slash, fragments, and query params."""
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip('/')
        return f"{parsed.scheme}://{parsed.netloc}{clean_path}"
    
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Extract article content from Tasnim News HTML page."""
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
                
                # Extract body - prefer article tag, fallback to content sections
                body = ''
                content_container = None
                
                # First try article tag
                article_elem = soup.find('article')
                if article_elem:
                    content_container = article_elem
                else:
                    # Fallback to content sections
                    content_selectors = [
                        '.content',
                        '.article-content',
                        '.story-body',
                        '.news-content',
                        '.post-content',
                        'main .content',
                        '.text-content'
                    ]
                    
                    for selector in content_selectors:
                        content_elem = soup.select_one(selector)
                        if content_elem:
                            content_container = content_elem
                            break
                    
                    # Last fallback to main tag
                    if not content_container:
                        main = soup.find('main')
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
        """Extract main image URL from Tasnim News HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Try to find main/featured image with Tasnim-specific selectors
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
            'img[class*="lead"]'
        ]
        
        for selector in featured_selectors:
            img_elem = soup.select_one(selector)
            if img_elem:
                src = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                if src:
                    img_url = urljoin(base_url, src)
                    # Ignore logos, icons, avatars
                    if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar']):
                        return img_url
        
        # If no featured image found, look in article for first large image
        article = soup.find('article')
        if article:
            img_tags = article.find_all('img')
        else:
            # Fallback to content sections
            content_elem = soup.select_one('.content, .article-content, .story-body')
            if content_elem:
                img_tags = content_elem.find_all('img', limit=5)  # Limit to first few images
            else:
                img_tags = []
        
        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                img_url = urljoin(base_url, src)
                # Ignore logos, icons, avatars, thumbnails
                if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'thumb', 'thumbnail', 'small']):
                    # Check if image seems large
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
                        # If no dimensions, assume it might be main image if not in skip list
                        return img_url
        
        return None

