"""
Tests for the web crawler module.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.crawler.scraper import (
    WebCrawler,
    CrawlConfig,
    CrawlResult,
    PageMetadata
)


class TestCrawlConfig:
    """Tests for CrawlConfig dataclass."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = CrawlConfig()
        assert config.max_pages == 50
        assert config.max_depth == 5
        assert config.timeout == 30000
        assert config.aggressive_mode is False
        assert config.parallel_workers == 3
        assert config.crawl_delay == 1.0
        assert config.respect_robots is True
        assert config.user_agent == "DevrozCrawler/1.0"
    
    def test_custom_config(self):
        """Test custom configuration values."""
        config = CrawlConfig(
            max_pages=100,
            max_depth=10,
            aggressive_mode=True,
            parallel_workers=5
        )
        assert config.max_pages == 100
        assert config.max_depth == 10
        assert config.aggressive_mode is True
        assert config.parallel_workers == 5


class TestPageMetadata:
    """Tests for PageMetadata dataclass."""
    
    def test_default_metadata(self):
        """Test default metadata values."""
        meta = PageMetadata(url="https://example.com")
        assert meta.url == "https://example.com"
        assert meta.title == ""
        assert meta.description == ""
        assert meta.canonical_url is None
        assert meta.noindex is False
        assert meta.nofollow is False
        assert meta.images == []
        assert meta.links == []
        assert meta.hreflang_tags == {}
    
    def test_full_metadata(self):
        """Test metadata with all fields."""
        meta = PageMetadata(
            url="https://example.com",
            title="Example",
            description="Test description",
            canonical_url="https://example.com/canonical",
            og_title="OG Title",
            og_description="OG Description",
            og_image="https://example.com/image.jpg",
            twitter_card="summary_large_image",
            noindex=True,
            nofollow=True
        )
        assert meta.title == "Example"
        assert meta.og_title == "OG Title"
        assert meta.twitter_card == "summary_large_image"
        assert meta.noindex is True


class TestCrawlResult:
    """Tests for CrawlResult dataclass."""
    
    def test_default_result(self):
        """Test default result values."""
        result = CrawlResult()
        assert result.pages == []
        assert result.discovered_urls == set()
        assert result.sitemap_urls == set()
        assert result.errors == []
        assert result.robots_data is None


class TestWebCrawler:
    """Tests for WebCrawler class."""
    
    def test_init_default_config(self):
        """Test crawler initialization with default config."""
        crawler = WebCrawler()
        assert crawler.config.max_pages == 50
        assert crawler.visited_urls == set()
        assert crawler.discovered_urls == set()
    
    def test_init_custom_config(self):
        """Test crawler initialization with custom config."""
        config = CrawlConfig(max_pages=100, aggressive_mode=True)
        crawler = WebCrawler(config)
        assert crawler.config.max_pages == 100
        assert crawler.config.aggressive_mode is True
    
    def test_common_paths_defined(self):
        """Test that common paths are defined."""
        assert len(WebCrawler.COMMON_PATHS) > 0
        assert "/robots.txt" in WebCrawler.COMMON_PATHS
        assert "/sitemap.xml" in WebCrawler.COMMON_PATHS
        assert "/admin" in WebCrawler.COMMON_PATHS
    
    def test_cms_patterns_defined(self):
        """Test that CMS patterns are defined."""
        assert "wordpress" in WebCrawler.CMS_PATTERNS
        assert "shopify" in WebCrawler.CMS_PATTERNS
        assert "drupal" in WebCrawler.CMS_PATTERNS
        
        # WordPress patterns
        assert "/wp-admin" in WebCrawler.CMS_PATTERNS["wordpress"]
        assert "/wp-json/wp/v2/posts" in WebCrawler.CMS_PATTERNS["wordpress"]
    
    def test_data_url_attrs_defined(self):
        """Test that data URL attributes are defined."""
        assert "data-href" in WebCrawler.DATA_URL_ATTRS
        assert "data-url" in WebCrawler.DATA_URL_ATTRS
        assert "data-src" in WebCrawler.DATA_URL_ATTRS
    
    def test_looks_like_url(self):
        """Test URL pattern detection."""
        crawler = WebCrawler()
        
        # Valid URLs
        assert crawler._looks_like_url("/page") is True
        assert crawler._looks_like_url("/page.html") is True
        assert crawler._looks_like_url("https://example.com") is True
        assert crawler._looks_like_url("./relative") is True
        
        # Invalid URLs
        assert crawler._looks_like_url("#anchor") is False
        assert crawler._looks_like_url("javascript:void(0)") is False
        assert crawler._looks_like_url("mailto:test@test.com") is False
        assert crawler._looks_like_url("tel:+123456789") is False
        assert crawler._looks_like_url("data:image/png") is False
        assert crawler._looks_like_url("") is False
    
    def test_parse_robots_content(self):
        """Test robots.txt parsing."""
        crawler = WebCrawler()
        
        content = """
User-agent: *
Allow: /
Disallow: /admin
Disallow: /private

Sitemap: https://example.com/sitemap.xml
Sitemap: https://example.com/sitemap2.xml

Crawl-delay: 2
        """
        
        result = crawler._parse_robots_content(content)
        
        assert len(result["sitemaps"]) == 2
        assert "https://example.com/sitemap.xml" in result["sitemaps"]
        assert "https://example.com/sitemap2.xml" in result["sitemaps"]
        assert "/admin" in result["disallowed"]
        assert "/private" in result["disallowed"]
        assert "/" in result["allowed"]
        assert result["crawl_delay"] == 2.0
    
    def test_parse_robots_content_no_delay(self):
        """Test robots.txt parsing without crawl-delay."""
        crawler = WebCrawler()
        
        content = """
User-agent: *
Allow: /
        """
        
        result = crawler._parse_robots_content(content)
        assert result["crawl_delay"] is None
    
    def test_parse_robots_content_invalid_delay(self):
        """Test robots.txt parsing with invalid crawl-delay."""
        crawler = WebCrawler()
        
        content = """
Crawl-delay: invalid
        """
        
        result = crawler._parse_robots_content(content)
        assert result["crawl_delay"] is None


class TestWebCrawlerAsync:
    """Async tests for WebCrawler."""
    
    @pytest.mark.asyncio
    async def test_parse_sitemap_xml(self):
        """Test parsing a standard sitemap XML."""
        crawler = WebCrawler()
        
        sitemap_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url>
                <loc>https://example.com/page1</loc>
            </url>
            <url>
                <loc>https://example.com/page2</loc>
            </url>
        </urlset>
        """
        
        # Mock the session
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=sitemap_content)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        
        urls, child_sitemaps = await crawler._parse_sitemap(
            mock_session, "https://example.com/sitemap.xml"
        )
        
        assert "https://example.com/page1" in urls
        assert "https://example.com/page2" in urls
        assert len(child_sitemaps) == 0
    
    @pytest.mark.asyncio
    async def test_parse_sitemap_index(self):
        """Test parsing a sitemap index file."""
        crawler = WebCrawler()
        
        sitemap_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap>
                <loc>https://example.com/sitemap1.xml</loc>
            </sitemap>
            <sitemap>
                <loc>https://example.com/sitemap2.xml</loc>
            </sitemap>
        </sitemapindex>
        """
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=sitemap_content)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        
        urls, child_sitemaps = await crawler._parse_sitemap(
            mock_session, "https://example.com/sitemap_index.xml"
        )
        
        assert len(urls) == 0
        assert "https://example.com/sitemap1.xml" in child_sitemaps
        assert "https://example.com/sitemap2.xml" in child_sitemaps


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
