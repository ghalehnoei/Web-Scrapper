"""Base class for news sources."""

import hashlib
import re
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any

import httpx


class BaseRSSSource(ABC):
    """Abstract base class for news sources (RSS or listing page)."""
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the source name identifier."""
        pass
    
    @property
    @abstractmethod
    def rss_url(self) -> str:
        """Return the RSS feed URL or listing page URL."""
        pass
    
    @abstractmethod
    async def fetch_rss_items(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch and parse RSS feed or listing page, return list of items."""
        pass
    
    @abstractmethod
    async def extract_article(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Extract article content from HTML page.
        
        Returns:
            Dict with keys: 'title', 'body' (HTML), 'main_image_url'
        """
        pass
    
    @abstractmethod
    def extract_main_image(self, html: str, base_url: str) -> Optional[str]:
        """Extract main image URL from HTML.
        
        Args:
            html: HTML content of the article page
            base_url: Base URL of the article for resolving relative URLs
            
        Returns:
            Main image URL or None
        """
        pass
    
    def extract_news_code(self, url: str) -> str:
        """Extract news code from URL. Can be overridden by subclasses."""
        # Default: extract numeric ID from URL
        match = re.search(r'/(\d+)/', url)
        if match:
            return match.group(1)
        match = re.search(r'/(\d+)$', url)
        if match:
            return match.group(1)
        return hashlib.md5(url.encode()).hexdigest()[:8]

