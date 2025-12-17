"""Mehr News Agency RSS source implementation."""

import asyncio
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


class MehrNewsSource(BaseRSSSource):
    """Mehr News Agency RSS source implementation."""
    
    def __init__(self, rss_url: str, timeout: float, retry_count: int, retry_delay: float):
        self._rss_url = rss_url
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
    
    @property
    def source_name(self) -> str:
        return 'mehrnews'
    
    @property
    def rss_url(self) -> str:
        return self._rss_url
    
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch and parse Mehr News RSS feed."""
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
    
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Extract article content from Mehr News HTML page."""
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
                    '.news-text'
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
        """Extract main image URL from Mehr News HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        
        featured_selectors = [
            'img.item-img',
            '.item-img img',
            'img.featured-image',
            'img.header-image',
            '.article-header img',
            '.news-header img',
            '.featured img',
            'header img',
            '.main-image',
            'img[class*="featured"]',
            'img[class*="header"]',
            'img[class*="main"]'
        ]
        
        for selector in featured_selectors:
            img_elem = soup.select_one(selector)
            if img_elem:
                src = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                if src:
                    img_url = urljoin(base_url, src)
                    if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar']):
                        return img_url
        
        article = soup.find('article') or soup.find('div', class_=re.compile('article|content|news', re.I))
        if article:
            img_tags = article.find_all('img')
        else:
            img_tags = soup.find_all('img')
        
        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                img_url = urljoin(base_url, src)
                if not any(skip in img_url.lower() for skip in ['logo', 'icon', 'avatar', 'thumb', 'thumbnail', 'small']):
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
                        return img_url
        
        return None

