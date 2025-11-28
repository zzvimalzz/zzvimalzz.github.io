"""
Advanced Web Crawler with Enhanced Discovery Capabilities

This module provides a comprehensive web crawler with features for:
- Sitemap parsing (XML, gzipped, sitemap index files)
- Hidden/dynamic page discovery
- JavaScript-based navigation detection
- Advanced link extraction from various sources
- Parallel crawling support
- Enhanced metadata extraction
"""

import asyncio
import gzip
import io
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Set, Dict, List, Any
from urllib.parse import urljoin, urlparse, parse_qs
from xml.etree import ElementTree as ET

import aiohttp
from playwright.async_api import async_playwright, Page, Browser


# Get logger - let the application configure logging
logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    """Configuration for the web crawler."""
    max_pages: int = 50
    max_depth: int = 5
    timeout: int = 30000  # milliseconds
    aggressive_mode: bool = False
    parallel_workers: int = 3
    crawl_delay: float = 1.0  # seconds
    respect_robots: bool = True
    user_agent: str = "DevrozCrawler/1.0"
    

@dataclass
class PageMetadata:
    """Metadata extracted from a crawled page."""
    url: str
    title: str = ""
    description: str = ""
    canonical_url: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    twitter_card: Optional[str] = None
    twitter_title: Optional[str] = None
    twitter_description: Optional[str] = None
    twitter_image: Optional[str] = None
    hreflang_tags: Dict[str, str] = field(default_factory=dict)
    noindex: bool = False
    nofollow: bool = False
    images: List[Dict[str, str]] = field(default_factory=list)
    links: List[str] = field(default_factory=list)


@dataclass
class CrawlResult:
    """Result of a complete crawl operation."""
    pages: List[PageMetadata] = field(default_factory=list)
    discovered_urls: Set[str] = field(default_factory=set)
    sitemap_urls: Set[str] = field(default_factory=set)
    errors: List[Dict[str, str]] = field(default_factory=list)
    robots_data: Optional[Dict[str, Any]] = None


class WebCrawler:
    """
    Advanced web crawler with enhanced discovery capabilities.
    
    Features:
    - Sitemap parsing (XML, gzipped, sitemap index)
    - Hidden/dynamic page discovery
    - JavaScript-based navigation detection
    - Advanced link extraction
    - Parallel crawling support
    - Enhanced metadata extraction
    """
    
    # Common paths for discovery
    COMMON_PATHS = [
        "/robots.txt", "/sitemap.xml", "/sitemap_index.xml",
        "/sitemap-index.xml", "/sitemaps.xml", "/sitemap.xml.gz",
        "/api", "/admin", "/login", "/logout", "/register", "/signup",
        "/404", "/search", "/contact", "/about", "/faq", "/help",
        "/terms", "/privacy", "/blog", "/news", "/products", "/services",
        "/categories", "/tags", "/archive", "/feed", "/rss", "/atom.xml"
    ]
    
    # CMS-specific patterns
    CMS_PATTERNS = {
        "wordpress": [
            "/wp-admin", "/wp-login.php", "/wp-content",
            "/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages",
            "/xmlrpc.php", "/feed"
        ],
        "shopify": [
            "/admin", "/collections", "/products.json",
            "/cart", "/checkout", "/account/login",
            "/pages", "/blogs"
        ],
        "drupal": [
            "/admin", "/user/login", "/node",
            "/sites/default", "/admin/content"
        ],
        "joomla": [
            "/administrator", "/components",
            "/modules", "/plugins"
        ],
        "magento": [
            "/admin", "/customer/account/login",
            "/catalog/category", "/checkout"
        ]
    }
    
    # URL patterns in data attributes
    DATA_URL_ATTRS = [
        "data-href", "data-url", "data-link", "data-src",
        "data-page", "data-target", "data-action", "data-route"
    ]
    
    # Pre-compiled regex for URL pattern matching
    URL_PATH_PATTERN = re.compile(r"^[\w\-/]+\.?(html?|php|aspx?|jsp)?$")
    
    def __init__(self, config: Optional[CrawlConfig] = None):
        """Initialize the crawler with configuration."""
        self.config = config or CrawlConfig()
        self.visited_urls: Set[str] = set()
        self.discovered_urls: Set[str] = set()
        self.sitemap_urls: Set[str] = set()
        self.pages: List[PageMetadata] = []
        self.errors: List[Dict[str, str]] = []
        self.robots_data: Optional[Dict[str, Any]] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        
    async def crawl(self, start_url: str) -> CrawlResult:
        """
        Perform a complete crawl starting from the given URL.
        
        Args:
            start_url: The URL to start crawling from
            
        Returns:
            CrawlResult containing all discovered pages and metadata
        """
        self.visited_urls.clear()
        self.discovered_urls.clear()
        self.sitemap_urls.clear()
        self.pages.clear()
        self.errors.clear()
        
        parsed = urlparse(start_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self._semaphore = asyncio.Semaphore(self.config.parallel_workers)
        
        # Phase 1: Parse robots.txt
        await self._parse_robots_txt()
        
        # Phase 2: Discover pages from sitemaps
        await self._discover_from_sitemaps()
        
        # Phase 3: Check common paths if aggressive mode
        if self.config.aggressive_mode:
            await self._check_common_paths()
            await self._check_cms_patterns()
        
        # Phase 4: Start crawling from the initial URL
        self.discovered_urls.add(start_url)
        await self._crawl_pages()
        
        return CrawlResult(
            pages=self.pages,
            discovered_urls=self.discovered_urls,
            sitemap_urls=self.sitemap_urls,
            errors=self.errors,
            robots_data=self.robots_data
        )
    
    async def _parse_robots_txt(self) -> None:
        """Parse robots.txt and extract sitemap URLs and crawl-delay."""
        robots_url = urljoin(self.base_url, "/robots.txt")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    robots_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": self.config.user_agent}
                ) as response:
                    if response.status == 200:
                        content = await response.text()
                        self.robots_data = self._parse_robots_content(content)
                        
                        # Extract sitemap URLs
                        for sitemap_url in self.robots_data.get("sitemaps", []):
                            self.sitemap_urls.add(sitemap_url)
                        
                        # Respect crawl-delay if configured
                        if self.config.respect_robots:
                            delay = self.robots_data.get("crawl_delay")
                            if delay and delay > self.config.crawl_delay:
                                self.config.crawl_delay = delay
                                logger.info(f"Updated crawl delay to {delay}s from robots.txt")
                                
        except Exception as e:
            logger.warning(f"Failed to parse robots.txt: {e}")
            self.errors.append({
                "url": robots_url,
                "error": str(e),
                "type": "robots_parse_error"
            })
    
    def _parse_robots_content(self, content: str) -> Dict[str, Any]:
        """Parse robots.txt content and extract directives."""
        result: Dict[str, Any] = {
            "sitemaps": [],
            "allowed": [],
            "disallowed": [],
            "crawl_delay": None
        }
        
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
                
            if ":" in line:
                directive, value = line.split(":", 1)
                directive = directive.strip().lower()
                value = value.strip()
                
                if directive == "sitemap":
                    result["sitemaps"].append(value)
                elif directive == "allow":
                    result["allowed"].append(value)
                elif directive == "disallow":
                    result["disallowed"].append(value)
                elif directive == "crawl-delay":
                    try:
                        result["crawl_delay"] = float(value)
                    except ValueError:
                        pass
        
        return result
    
    async def _discover_from_sitemaps(self) -> None:
        """Discover pages from sitemap.xml files."""
        # Add default sitemap if none found in robots.txt
        if not self.sitemap_urls:
            self.sitemap_urls.add(urljoin(self.base_url, "/sitemap.xml"))
        
        sitemaps_to_process = list(self.sitemap_urls)
        processed_sitemaps: Set[str] = set()
        
        async with aiohttp.ClientSession() as session:
            while sitemaps_to_process:
                sitemap_url = sitemaps_to_process.pop(0)
                
                if sitemap_url in processed_sitemaps:
                    continue
                    
                processed_sitemaps.add(sitemap_url)
                
                try:
                    urls, child_sitemaps = await self._parse_sitemap(
                        session, sitemap_url
                    )
                    self.discovered_urls.update(urls)
                    
                    # Add child sitemaps for processing
                    for child in child_sitemaps:
                        if child not in processed_sitemaps:
                            sitemaps_to_process.append(child)
                            self.sitemap_urls.add(child)
                            
                except Exception as e:
                    logger.warning(f"Failed to parse sitemap {sitemap_url}: {e}")
                    self.errors.append({
                        "url": sitemap_url,
                        "error": str(e),
                        "type": "sitemap_parse_error"
                    })
    
    async def _parse_sitemap(
        self, session: aiohttp.ClientSession, sitemap_url: str
    ) -> tuple[Set[str], Set[str]]:
        """
        Parse a sitemap and return discovered URLs.
        
        Handles:
        - Standard XML sitemaps
        - Sitemap index files
        - Gzipped sitemaps (.xml.gz)
        
        Returns:
            Tuple of (page_urls, child_sitemap_urls)
        """
        urls: Set[str] = set()
        child_sitemaps: Set[str] = set()
        
        async with session.get(
            sitemap_url,
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": self.config.user_agent}
        ) as response:
            if response.status != 200:
                return urls, child_sitemaps
            
            content = await response.read()
            
            # Handle gzipped sitemaps
            if sitemap_url.endswith(".gz"):
                try:
                    content = gzip.decompress(content)
                except gzip.BadGzipFile:
                    logger.warning(f"Failed to decompress gzipped sitemap: {sitemap_url}")
                    return urls, child_sitemaps
            
            try:
                root = ET.fromstring(content)
            except ET.ParseError as e:
                logger.warning(f"Failed to parse sitemap XML: {e}")
                return urls, child_sitemaps
            
            # Define namespace
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            
            # Check for sitemap index (sitemaps of sitemaps)
            for sitemap in root.findall(".//sm:sitemap/sm:loc", ns):
                if sitemap.text:
                    child_sitemaps.add(sitemap.text.strip())
            
            # Extract page URLs
            for url in root.findall(".//sm:url/sm:loc", ns):
                if url.text:
                    urls.add(url.text.strip())
            
            # Fallback: try without namespace
            if not urls and not child_sitemaps:
                for sitemap in root.findall(".//sitemap/loc"):
                    if sitemap.text:
                        child_sitemaps.add(sitemap.text.strip())
                        
                for url in root.findall(".//url/loc"):
                    if url.text:
                        urls.add(url.text.strip())
        
        return urls, child_sitemaps
    
    async def _check_common_paths(self) -> None:
        """Check common paths for hidden pages."""
        async with aiohttp.ClientSession() as session:
            tasks = []
            for path in self.COMMON_PATHS:
                url = urljoin(self.base_url, path)
                tasks.append(self._check_path_exists(session, url))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, str):
                    self.discovered_urls.add(result)
    
    async def _check_cms_patterns(self) -> None:
        """Check for CMS-specific patterns."""
        async with aiohttp.ClientSession() as session:
            for cms_name, paths in self.CMS_PATTERNS.items():
                for path in paths:
                    url = urljoin(self.base_url, path)
                    try:
                        result = await self._check_path_exists(session, url)
                        if result:
                            self.discovered_urls.add(result)
                            logger.info(f"Detected {cms_name} CMS pattern: {path}")
                    except Exception:
                        pass
    
    async def _check_path_exists(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[str]:
        """Check if a path exists and is accessible."""
        try:
            async with session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": self.config.user_agent},
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    return str(response.url)
        except Exception:
            pass
        return None
    
    async def _crawl_pages(self) -> None:
        """Crawl all discovered pages using Playwright."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            
            try:
                pages_to_crawl = list(self.discovered_urls)
                depth_map: Dict[str, int] = {url: 0 for url in pages_to_crawl}
                
                while pages_to_crawl and len(self.visited_urls) < self.config.max_pages:
                    # Get batch of URLs to process
                    batch_size = min(
                        self.config.parallel_workers,
                        len(pages_to_crawl),
                        self.config.max_pages - len(self.visited_urls)
                    )
                    batch = []
                    
                    for _ in range(batch_size):
                        if not pages_to_crawl:
                            break
                        url = pages_to_crawl.pop(0)
                        if url not in self.visited_urls:
                            current_depth = depth_map.get(url, 0)
                            if current_depth <= self.config.max_depth:
                                batch.append((url, current_depth))
                    
                    # Process batch in parallel
                    tasks = [
                        self._crawl_single_page(browser, url, depth)
                        for url, depth in batch
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Process results and discover new URLs
                    for result in results:
                        if isinstance(result, tuple):
                            page_meta, new_urls, depth = result
                            if page_meta:
                                self.pages.append(page_meta)
                                
                            # Add new URLs with updated depth
                            for new_url in new_urls:
                                if new_url not in self.visited_urls:
                                    if new_url not in depth_map:
                                        depth_map[new_url] = depth + 1
                                        pages_to_crawl.append(new_url)
                    
                    # Respect crawl delay
                    if pages_to_crawl:
                        await asyncio.sleep(self.config.crawl_delay)
                        
            finally:
                await browser.close()
    
    async def _crawl_single_page(
        self, browser: Browser, url: str, depth: int
    ) -> tuple[Optional[PageMetadata], Set[str], int]:
        """Crawl a single page and extract metadata and links."""
        async with self._semaphore:
            if url in self.visited_urls:
                return None, set(), depth
                
            self.visited_urls.add(url)
            new_urls: Set[str] = set()
            
            try:
                context = await browser.new_context(
                    user_agent=self.config.user_agent
                )
                page = await context.new_page()
                
                try:
                    await page.goto(
                        url,
                        timeout=self.config.timeout,
                        wait_until="networkidle"
                    )
                    
                    # Extract page metadata
                    metadata = await self._extract_page_metadata(page, url)
                    
                    # Extract links from various sources
                    new_urls = await self._extract_all_links(page)
                    
                    # Filter URLs to same domain
                    parsed_base = urlparse(self.base_url)
                    new_urls = {
                        u for u in new_urls
                        if urlparse(u).netloc == parsed_base.netloc
                    }
                    
                    return metadata, new_urls, depth
                    
                finally:
                    await context.close()
                    
            except Exception as e:
                logger.warning(f"Failed to crawl {url}: {e}")
                self.errors.append({
                    "url": url,
                    "error": str(e),
                    "type": "crawl_error"
                })
                return None, set(), depth
    
    async def _extract_page_metadata(
        self, page: Page, url: str
    ) -> PageMetadata:
        """Extract comprehensive metadata from a page."""
        metadata = PageMetadata(url=url)
        
        try:
            # Basic metadata
            metadata.title = await page.title() or ""
            
            # Meta description
            meta_desc = await page.query_selector('meta[name="description"]')
            if meta_desc:
                metadata.description = await meta_desc.get_attribute("content") or ""
            
            # Canonical URL
            canonical = await page.query_selector('link[rel="canonical"]')
            if canonical:
                metadata.canonical_url = await canonical.get_attribute("href")
            
            # Open Graph tags
            og_title = await page.query_selector('meta[property="og:title"]')
            if og_title:
                metadata.og_title = await og_title.get_attribute("content")
                
            og_desc = await page.query_selector('meta[property="og:description"]')
            if og_desc:
                metadata.og_description = await og_desc.get_attribute("content")
                
            og_image = await page.query_selector('meta[property="og:image"]')
            if og_image:
                metadata.og_image = await og_image.get_attribute("content")
            
            # Twitter Card tags
            twitter_card = await page.query_selector('meta[name="twitter:card"]')
            if twitter_card:
                metadata.twitter_card = await twitter_card.get_attribute("content")
                
            twitter_title = await page.query_selector('meta[name="twitter:title"]')
            if twitter_title:
                metadata.twitter_title = await twitter_title.get_attribute("content")
                
            twitter_desc = await page.query_selector('meta[name="twitter:description"]')
            if twitter_desc:
                metadata.twitter_description = await twitter_desc.get_attribute("content")
                
            twitter_image = await page.query_selector('meta[name="twitter:image"]')
            if twitter_image:
                metadata.twitter_image = await twitter_image.get_attribute("content")
            
            # Hreflang tags
            hreflang_links = await page.query_selector_all('link[rel="alternate"][hreflang]')
            for link in hreflang_links:
                hreflang = await link.get_attribute("hreflang")
                href = await link.get_attribute("href")
                if hreflang and href:
                    metadata.hreflang_tags[hreflang] = href
            
            # Robots meta tag
            robots_meta = await page.query_selector('meta[name="robots"]')
            if robots_meta:
                robots_content = await robots_meta.get_attribute("content") or ""
                robots_content = robots_content.lower()
                metadata.noindex = "noindex" in robots_content
                metadata.nofollow = "nofollow" in robots_content
            
            # Extract images with alt text
            images = await page.query_selector_all("img")
            for img in images:
                src = await img.get_attribute("src")
                alt = await img.get_attribute("alt")
                srcset = await img.get_attribute("srcset")
                
                if src:
                    metadata.images.append({
                        "src": urljoin(url, src),
                        "alt": alt or "",
                        "srcset": srcset or ""
                    })
            
        except Exception as e:
            logger.warning(f"Error extracting metadata from {url}: {e}")
        
        return metadata
    
    async def _extract_all_links(self, page: Page) -> Set[str]:
        """
        Extract all links from a page using multiple methods.
        
        Extracts from:
        - Standard anchor tags
        - JavaScript onclick handlers
        - Data attributes (data-href, data-url, etc.)
        - Srcset attributes
        - CSS background-image URLs
        - Lazy-loaded content
        - Pagination links
        """
        links: Set[str] = set()
        current_url = page.url
        
        try:
            # Standard anchor tags
            anchors = await page.query_selector_all("a[href]")
            for anchor in anchors:
                href = await anchor.get_attribute("href")
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    links.add(urljoin(current_url, href))
            
            # Extract from onclick handlers
            onclick_links = await self._extract_onclick_urls(page, current_url)
            links.update(onclick_links)
            
            # Extract from data attributes
            data_links = await self._extract_data_attribute_urls(page, current_url)
            links.update(data_links)
            
            # Extract from srcset
            srcset_links = await self._extract_srcset_urls(page, current_url)
            links.update(srcset_links)
            
            # Extract from CSS background-image
            css_links = await self._extract_css_background_urls(page, current_url)
            links.update(css_links)
            
            # Extract pagination links
            pagination_links = await self._extract_pagination_urls(page, current_url)
            links.update(pagination_links)
            
            # Extract from JavaScript router/navigation
            js_routes = await self._extract_js_routes(page, current_url)
            links.update(js_routes)
            
        except Exception as e:
            logger.warning(f"Error extracting links: {e}")
        
        return links
    
    async def _extract_onclick_urls(self, page: Page, base_url: str) -> Set[str]:
        """Extract URLs from onclick handlers."""
        urls: Set[str] = set()
        
        try:
            elements = await page.query_selector_all("[onclick]")
            for el in elements:
                onclick = await el.get_attribute("onclick") or ""
                
                # Match patterns like window.location, location.href, etc.
                patterns = [
                    r"window\.location\s*=\s*['\"]([^'\"]+)['\"]",
                    r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
                    r"window\.open\(['\"]([^'\"]+)['\"]",
                    r"navigate\(['\"]([^'\"]+)['\"]",
                    r"goto\(['\"]([^'\"]+)['\"]",
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, onclick)
                    for match in matches:
                        if not match.startswith(("javascript:", "#")):
                            urls.add(urljoin(base_url, match))
                            
        except Exception as e:
            logger.debug(f"Error extracting onclick URLs: {e}")
        
        return urls
    
    async def _extract_data_attribute_urls(
        self, page: Page, base_url: str
    ) -> Set[str]:
        """Extract URLs from data attributes."""
        urls: Set[str] = set()
        
        try:
            for attr in self.DATA_URL_ATTRS:
                elements = await page.query_selector_all(f"[{attr}]")
                for el in elements:
                    value = await el.get_attribute(attr) or ""
                    if value and self._looks_like_url(value):
                        urls.add(urljoin(base_url, value))
                        
        except Exception as e:
            logger.debug(f"Error extracting data attribute URLs: {e}")
        
        return urls
    
    async def _extract_srcset_urls(self, page: Page, base_url: str) -> Set[str]:
        """Extract URLs from srcset attributes."""
        urls: Set[str] = set()
        
        try:
            elements = await page.query_selector_all("[srcset]")
            for el in elements:
                srcset = await el.get_attribute("srcset") or ""
                
                # Parse srcset format: "url1 1x, url2 2x" or "url1 100w, url2 200w"
                for part in srcset.split(","):
                    part = part.strip()
                    if part:
                        # Extract URL (first part before space)
                        url_part = part.split()[0] if part.split() else part
                        if url_part:
                            urls.add(urljoin(base_url, url_part))
                            
        except Exception as e:
            logger.debug(f"Error extracting srcset URLs: {e}")
        
        return urls
    
    async def _extract_css_background_urls(
        self, page: Page, base_url: str
    ) -> Set[str]:
        """Extract URLs from CSS background-image properties."""
        urls: Set[str] = set()
        
        try:
            # Get all stylesheets with rule count limit for performance
            result = await page.evaluate("""
                () => {
                    const urls = [];
                    const sheets = document.styleSheets;
                    const MAX_RULES = 1000;  // Limit to prevent performance issues
                    let ruleCount = 0;
                    
                    for (let i = 0; i < sheets.length && ruleCount < MAX_RULES; i++) {
                        try {
                            const rules = sheets[i].cssRules || sheets[i].rules;
                            for (let j = 0; j < rules.length && ruleCount < MAX_RULES; j++) {
                                ruleCount++;
                                const rule = rules[j];
                                if (rule.style && rule.style.backgroundImage) {
                                    const matches = rule.style.backgroundImage.match(/url\\(['"]?([^'")]+)['"]?\\)/g);
                                    if (matches) {
                                        matches.forEach(m => {
                                            const url = m.replace(/url\\(['"]?|['"]?\\)/g, '');
                                            urls.push(url);
                                        });
                                    }
                                }
                            }
                        } catch (e) {
                            // CORS may prevent access to external stylesheets
                        }
                    }
                    return urls;
                }
            """)
            
            for url in result:
                if url and not url.startswith("data:"):
                    urls.add(urljoin(base_url, url))
                    
        except Exception as e:
            logger.debug(f"Error extracting CSS background URLs: {e}")
        
        return urls
    
    async def _extract_pagination_urls(
        self, page: Page, base_url: str
    ) -> Set[str]:
        """Extract pagination URLs."""
        urls: Set[str] = set()
        
        try:
            # Common pagination selectors
            pagination_selectors = [
                ".pagination a[href]",
                ".pager a[href]",
                "[class*='pagination'] a[href]",
                "[class*='paging'] a[href]",
                "nav[aria-label*='pagination'] a[href]",
                ".page-numbers a[href]",
                "[rel='next']",
                "[rel='prev']",
                "a[aria-label*='page']",
                "a[aria-label*='next']",
                "a[aria-label*='previous']",
            ]
            
            for selector in pagination_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        href = await el.get_attribute("href")
                        if href and not href.startswith(("#", "javascript:")):
                            urls.add(urljoin(base_url, href))
                except Exception:
                    pass
                    
        except Exception as e:
            logger.debug(f"Error extracting pagination URLs: {e}")
        
        return urls
    
    async def _extract_js_routes(self, page: Page, base_url: str) -> Set[str]:
        """Extract URLs from JavaScript-based routing."""
        urls: Set[str] = set()
        
        try:
            # Try to extract routes from common SPA frameworks
            # Content length limit to prevent performance issues on large bundles
            result = await page.evaluate("""
                () => {
                    const routes = [];
                    const MAX_SCRIPT_LENGTH = 100000;  // 100KB limit per script
                    const MAX_SCRIPTS = 50;  // Limit number of scripts to parse
                    
                    // Check for React Router
                    if (window.__REACT_ROUTER_STATE__) {
                        const state = window.__REACT_ROUTER_STATE__;
                        if (state.routes) {
                            state.routes.forEach(r => {
                                if (r.path) routes.push(r.path);
                            });
                        }
                    }
                    
                    // Check for Vue Router
                    if (window.__VUE_ROUTER__) {
                        const router = window.__VUE_ROUTER__;
                        if (router.options && router.options.routes) {
                            router.options.routes.forEach(r => {
                                if (r.path) routes.push(r.path);
                            });
                        }
                    }
                    
                    // Check for Next.js
                    if (window.__NEXT_DATA__ && window.__NEXT_DATA__.pages) {
                        Object.keys(window.__NEXT_DATA__.pages).forEach(p => {
                            routes.push(p);
                        });
                    }
                    
                    // Look for href in internal links within script tags
                    // with limits to prevent performance issues
                    const scripts = document.querySelectorAll('script');
                    let scriptCount = 0;
                    
                    scripts.forEach(script => {
                        if (scriptCount >= MAX_SCRIPTS) return;
                        scriptCount++;
                        
                        const content = script.textContent || '';
                        // Skip large scripts to prevent performance issues
                        if (content.length > MAX_SCRIPT_LENGTH) return;
                        
                        const matches = content.match(/["']\\/([\\/\\w-]+)["']/g);
                        if (matches) {
                            matches.forEach(m => {
                                const path = m.replace(/['"]/g, '');
                                if (path.startsWith('/') && !path.includes('.')) {
                                    routes.push(path);
                                }
                            });
                        }
                    });
                    
                    return routes;
                }
            """)
            
            for route in result:
                if route and route.startswith("/"):
                    urls.add(urljoin(base_url, route))
                    
        except Exception as e:
            logger.debug(f"Error extracting JS routes: {e}")
        
        return urls
    
    def _looks_like_url(self, value: str) -> bool:
        """Check if a string looks like a URL or path."""
        if not value:
            return False
            
        # Skip obvious non-URLs
        if value.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return False
            
        # Check for common URL patterns
        if value.startswith(("/", "http://", "https://", "./")):
            return True
            
        # Check for path-like patterns using pre-compiled regex
        if self.URL_PATH_PATTERN.match(value):
            return True
            
        return False


async def main():
    """Example usage of the WebCrawler."""
    config = CrawlConfig(
        max_pages=50,
        max_depth=3,
        aggressive_mode=True,
        parallel_workers=3,
        crawl_delay=1.0
    )
    
    crawler = WebCrawler(config)
    result = await crawler.crawl("https://example.com")
    
    print(f"Discovered {len(result.discovered_urls)} URLs")
    print(f"Crawled {len(result.pages)} pages")
    print(f"Found {len(result.sitemap_urls)} sitemaps")
    print(f"Encountered {len(result.errors)} errors")
    
    for page in result.pages[:5]:
        print(f"\n{page.title or 'No title'} - {page.url}")
        if page.canonical_url:
            print(f"  Canonical: {page.canonical_url}")
        if page.images:
            print(f"  Images: {len(page.images)}")


if __name__ == "__main__":
    asyncio.run(main())
