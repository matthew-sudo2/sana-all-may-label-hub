"""
scraper.py — Autonomous Multi-Domain Data Discovery & Acquisition
=================================================================
PURPOSE
-------
Automatically discovers, evaluates, and downloads datasets from multiple
platforms without any hardcoded URLs. Given a list of domain keywords,
the scraper searches across Kaggle, UCI, Google Dataset Search, open data
portals, and the open web — then scores candidates by quality signals
(row count, column count, CSV availability, recency) before downloading.

HOW IT WORKS
------------
  1. DISCOVER  — For each domain keyword, query every search adapter in
                 parallel. Each adapter returns a list of DatasetCandidate
                 objects with metadata (title, url, estimated size, source).

  2. RANK      — Score candidates by quality signals. Deduplicate by URL.
                 Select the top-N per domain per source.

  3. ACQUIRE   — Download each selected candidate. Parse CSVs, flatten
                 JSON APIs, scrape HTML tables, or extract from ZIP/archives.

  4. VALIDATE  — Enforce min_rows, drop malformed files, strip HTML artifacts.

  5. DEPOSIT   — Write clean CSVs to ./datasets/<domain>/<slug>.csv
                 Update manifest.json with full provenance.

SEARCH ADAPTERS
---------------
  - KaggleSearchAdapter     searches Kaggle dataset catalogue via kaggle-api
  - UCISearchAdapter        crawls the UCI ML Repository dataset index
  - GoogleDatasetAdapter    queries Google Dataset Search (datasetsearch.research.google.com)
  - DataGovAdapter          hits the data.gov CKAN search API
  - DataEuropaAdapter       hits data.europa.eu SPARQL / search API
  - OpenDataSoftAdapter     searches public OpenDataSoft portals
  - GithubSearchAdapter     searches GitHub for CSV/data repositories
  - DDGSearchAdapter        DuckDuckGo HTML search — finds CSV links across the open web

DOMAINS SEARCHED (configurable)
--------------------------------
  financial, healthcare, government, ecommerce, scientific,
  social, geospatial, sensor, sports, climate, education, transport

DEPENDENCIES
------------
  Required : httpx, beautifulsoup4, pandas, tenacity, aiofiles, kaggle
  Optional : playwright  (JS-rendered pages)  pip install playwright && playwright install chromium
             duckduckgo-search                pip install duckduckgo-search

USAGE
-----
  python scraper.py                            # discover + download all domains
  python scraper.py --domains financial sports # specific domains
  python scraper.py --adapters kaggle uci      # specific adapters only
  python scraper.py --per-domain 10            # datasets per domain (default 5)
  python scraper.py --dry-run                  # discover only, no downloads
  python scraper.py --workers 12               # concurrency
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlencode, quote_plus
from urllib.robotparser import RobotFileParser

import aiofiles
import httpx
import pandas as pd
from bs4 import BeautifulSoup
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("scraper")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScraperConfig:
    output_dir: str         = "./datasets"
    manifest_file: str      = "./datasets/manifest.json"
    max_concurrency: int    = 8
    request_timeout: float  = 30.0
    min_rows: int           = 50
    max_file_mb: float      = 150.0
    per_domain: int         = 5          # target datasets per domain per adapter
    user_agent: str         = (
        "Mozilla/5.0 (compatible; DataQualityResearchBot/2.0; "
        "educational use; +https://github.com/your-org/data-quality)"
    )
    respect_robots: bool    = True
    rate_limit_delay: float = 1.5        # seconds between requests to same domain
    dry_run: bool           = False      # discover only, skip downloads


CFG = ScraperConfig()

# Domains and their search keywords (what we ask each adapter to find)
DOMAIN_QUERIES: dict[str, list[str]] = {
    "financial":   ["stock prices dataset", "financial transactions csv",
                    "economic indicators dataset", "cryptocurrency prices"],
    "healthcare":  ["patient health records dataset", "disease statistics csv",
                    "clinical trials data", "hospital admissions dataset"],
    "government":  ["government spending csv", "public census data",
                    "election results dataset", "crime statistics csv"],
    "ecommerce":   ["online retail transactions", "product sales dataset csv",
                    "customer purchase history", "ecommerce orders data"],
    "scientific":  ["scientific measurements dataset", "laboratory experiments csv",
                    "physics chemistry dataset", "materials properties data"],
    "social":      ["social media dataset csv", "demographic survey data",
                    "population statistics", "education outcomes dataset"],
    "geospatial":  ["geographic coordinates dataset", "city coordinates csv",
                    "country boundary data", "location poi dataset"],
    "sensor":      ["iot sensor readings csv", "weather station data",
                    "air quality measurements", "temperature humidity dataset"],
    "sports":      ["athlete statistics dataset", "game scores csv",
                    "sports performance data", "match results dataset"],
    "climate":     ["climate change dataset csv", "rainfall temperature data",
                    "greenhouse gas emissions", "sea level measurements"],
    "education":   ["student performance dataset", "school test scores csv",
                    "university rankings data", "literacy rates dataset"],
    "transport":   ["traffic flow dataset csv", "flight delays data",
                    "public transport ridership", "vehicle accident statistics"],
}

# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DatasetCandidate:
    """A discovered dataset before download."""
    title: str
    url: str                        # direct CSV URL or landing page
    domain: str
    source_adapter: str
    description: str    = ""
    file_format: str    = "unknown" # csv, json, zip, html_table, api
    estimated_rows: int = 0
    score: float        = 0.0
    meta: dict          = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """Stable slug derived from URL hash + sanitised title."""
        h     = hashlib.md5(self.url.encode()).hexdigest()[:6]
        title = re.sub(r"[^\w]", "_", self.title.lower())[:40].strip("_")
        return f"{self.source_adapter}_{title}_{h}"


@dataclass
class AcquiredDataset:
    candidate: DatasetCandidate
    path: Path
    rows: int
    columns: int
    elapsed_s: float
    status: str   = "ok"
    error: str    = ""


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

_robots_cache: dict[str, RobotFileParser] = {}
_rate_last: dict[str, float] = {}


def can_fetch(url: str) -> bool:
    if not CFG.respect_robots:
        return True
    parsed = urlparse(url)
    base   = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        rp = RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            pass
        _robots_cache[base] = rp
    return _robots_cache[base].can_fetch(CFG.user_agent, url)


async def throttled_get(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    domain = urlparse(url).netloc
    gap    = time.monotonic() - _rate_last.get(domain, 0.0)
    if gap < CFG.rate_limit_delay:
        await asyncio.sleep(CFG.rate_limit_delay - gap)
    _rate_last[domain] = time.monotonic()
    return await client.get(url, **kwargs)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=12),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
async def robust_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    resp = await throttled_get(client, url, **kwargs)
    resp.raise_for_status()
    return resp


def output_path(domain: str, slug: str) -> Path:
    folder = Path(CFG.output_dir) / domain
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{slug}.csv"


def score_candidate(c: DatasetCandidate) -> float:
    """
    Heuristic quality score for ranking candidates before download.
    Higher is better.
    """
    s = 0.0
    # Prefer known CSV format — highest signal for our pipeline
    fmt = c.file_format.lower()
    if fmt == "csv":
        s += 40
    elif fmt in ("zip", "json"):
        s += 20
    elif fmt == "html_table":
        s += 10
    # Estimated size (more rows = richer quality signal)
    if c.estimated_rows > 100_000:
        s += 30
    elif c.estimated_rows > 10_000:
        s += 20
    elif c.estimated_rows > 1_000:
        s += 10
    elif c.estimated_rows > 0:
        s += 5
    # Prefer direct download links
    url_lower = c.url.lower()
    if any(url_lower.endswith(ext) for ext in (".csv", ".csv.gz", ".zip")):
        s += 15
    # Penalise very short descriptions (low information)
    if len(c.description) > 100:
        s += 5
    return s


def clean_df(df: pd.DataFrame, candidate: DatasetCandidate) -> pd.DataFrame:
    """Strip HTML artefacts, add provenance columns, drop all-null rows."""
    for col in df.select_dtypes(include="object").columns:
        df[col] = (
            df[col].astype(str)
            .str.replace(r"<[^>]+>", "", regex=True)
            .str.strip()
            .replace("nan", pd.NA)
        )
    df = df.dropna(how="all")
    df["_source"]     = candidate.slug
    df["_domain"]     = candidate.domain
    df["_adapter"]    = candidate.source_adapter
    df["_origin_url"] = candidate.url
    df["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH ADAPTERS
# ─────────────────────────────────────────────────────────────────────────────

class BaseAdapter:
    name: str = "base"

    async def search(
        self,
        query: str,
        domain: str,
        client: httpx.AsyncClient,
        limit: int = 10,
    ) -> list[DatasetCandidate]:
        raise NotImplementedError


# ── 1. KAGGLE ─────────────────────────────────────────────────────────────────

class KaggleSearchAdapter(BaseAdapter):
    """
    Uses the official Kaggle API to search the dataset catalogue.
    Returns the top-N results sorted by votes (quality proxy).
    Requires ~/.kaggle/kaggle.json credentials.
    """
    name = "kaggle"

    def _search_sync(self, query: str, domain: str, limit: int) -> list[DatasetCandidate]:
        try:
            from kaggle.api.kaggle_api_extended import KaggleApiExtended
        except ImportError:
            log.warning("kaggle package not installed. Skipping Kaggle adapter.")
            return []
        try:
            api = KaggleApiExtended()
            api.authenticate()
            results = api.dataset_list(search=query, sort_by="votes", page=1)
        except Exception as exc:
            log.warning("[kaggle] Search failed for '%s': %s", query, exc)
            return []

        candidates = []
        for ds in results[:limit]:
            try:
                ref    = f"{ds.ref}"          # "owner/dataset-slug"
                title  = getattr(ds, "title", ref)
                size   = getattr(ds, "totalBytes", 0) or 0
                # Estimate rows: assume average row ~200 bytes for tabular data
                est_rows = max(0, int(size / 200))
                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = ref,           # Kaggle ref, not HTTP URL
                    domain         = domain,
                    source_adapter = self.name,
                    description    = getattr(ds, "subtitle", ""),
                    file_format    = "kaggle_ref",
                    estimated_rows = est_rows,
                    meta           = {"ref": ref, "size_bytes": size},
                ))
            except Exception:
                continue
        return candidates

    async def search(self, query, domain, client, limit=10):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query, domain, limit)

    def download_sync(self, candidate: DatasetCandidate) -> Optional[list[pd.DataFrame]]:
        try:
            from kaggle.api.kaggle_api_extended import KaggleApiExtended
        except ImportError:
            return None
        api = KaggleApiExtended()
        api.authenticate()
        tmp = Path(CFG.output_dir) / "_tmp_kaggle" / candidate.slug
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            api.dataset_download_files(candidate.meta["ref"], path=str(tmp), unzip=True, quiet=True)
        except Exception as exc:
            log.error("[kaggle] Download failed for %s: %s", candidate.slug, exc)
            return None
        dfs = []
        for csv_path in list(tmp.rglob("*.csv"))[:5]:
            try:
                dfs.append(pd.read_csv(csv_path, low_memory=False))
            except Exception:
                pass
        return dfs or None


# ── 2. UCI ML REPOSITORY ──────────────────────────────────────────────────────

class UCISearchAdapter(BaseAdapter):
    """
    Searches the UCI ML Repository dataset index page.
    Parses the HTML table to find datasets matching the query keyword.
    """
    name       = "uci"
    INDEX_URL  = "https://archive.ics.uci.edu/datasets"

    async def search(self, query, domain, client, limit=10):
        # UCI has a search endpoint
        search_url = f"{self.INDEX_URL}?search={quote_plus(query)}&skip=0&take={limit}"
        try:
            resp = await robust_get(client, search_url)
        except Exception as exc:
            log.warning("[uci] Index fetch failed: %s", exc)
            return []

        soup       = BeautifulSoup(resp.text, "html.parser")
        candidates = []

        # UCI new site renders dataset cards
        for card in soup.select("div[class*='dataset']")[:limit]:
            try:
                title_el = card.select_one("h2, h3, [class*='name'], a")
                link_el  = card.select_one("a[href]")
                if not title_el or not link_el:
                    continue
                title    = title_el.get_text(strip=True)
                href     = link_el["href"]
                full_url = href if href.startswith("http") else f"https://archive.ics.uci.edu{href}"

                # Extract dataset ID to build direct download URL
                did_match = re.search(r"/dataset/(\d+)", href)
                if not did_match:
                    continue
                did      = did_match.group(1)
                csv_url  = f"https://archive.ics.uci.edu/static/public/{did}/data.csv"

                desc_el  = card.select_one("p, [class*='description']")
                desc     = desc_el.get_text(strip=True) if desc_el else ""

                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = csv_url,
                    domain         = domain,
                    source_adapter = self.name,
                    description    = desc,
                    file_format    = "csv",
                    meta           = {"landing": full_url, "uci_id": did},
                ))
            except Exception:
                continue

        # Fallback: try older UCI archive search if new site returned nothing
        if not candidates:
            candidates = await self._legacy_search(query, domain, client, limit)

        return candidates

    async def _legacy_search(self, query, domain, client, limit):
        """Search old UCI archive index as fallback."""
        old_url = (
            "https://archive.ics.uci.edu/ml/datasets.php?"
            + urlencode({"Search": query})
        )
        try:
            resp = await robust_get(client, old_url)
        except Exception:
            return []

        soup       = BeautifulSoup(resp.text, "html.parser")
        candidates = []
        for row in soup.select("table tr")[1:limit + 1]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            try:
                link    = cells[0].find("a")
                if not link:
                    continue
                title   = link.get_text(strip=True)
                href    = link["href"]
                landing = f"https://archive.ics.uci.edu/ml/{href}"
                # Try to find a direct CSV on the landing page (async)
                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = landing,
                    domain         = domain,
                    source_adapter = self.name,
                    description    = cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    file_format    = "html_table",
                    meta           = {"landing": landing},
                ))
            except Exception:
                continue
        return candidates


# ── 3. DATA.GOV (CKAN API) ────────────────────────────────────────────────────

class DataGovAdapter(BaseAdapter):
    """
    Queries the data.gov CKAN API — the US federal open data portal.
    Searches for CSV resources matching the query.
    """
    name     = "datagov"
    BASE_URL = "https://catalog.data.gov/api/3/action/package_search"

    async def search(self, query, domain, client, limit=10):
        params = {
            "q":            query,
            "rows":         limit * 2,   # overfetch, filter to CSV below
            "fq":           'res_format:"CSV"',
            "sort":         "score desc",
        }
        try:
            resp = await robust_get(client, self.BASE_URL, params=params)
            data = resp.json()
        except Exception as exc:
            log.warning("[datagov] Search failed for '%s': %s", query, exc)
            return []

        candidates = []
        for pkg in data.get("result", {}).get("results", [])[:limit]:
            try:
                title = pkg.get("title", "")
                desc  = pkg.get("notes", "")
                for resource in pkg.get("resources", []):
                    if resource.get("format", "").upper() != "CSV":
                        continue
                    url = resource.get("url", "")
                    if not url:
                        continue
                    candidates.append(DatasetCandidate(
                        title          = title,
                        url            = url,
                        domain         = domain,
                        source_adapter = self.name,
                        description    = desc[:300],
                        file_format    = "csv",
                        meta           = {"package_id": pkg.get("id")},
                    ))
                    break   # one CSV resource per package is enough
            except Exception:
                continue
        return candidates


# ── 4. DATA EUROPA (EU Open Data Portal) ─────────────────────────────────────

class DataEuropaAdapter(BaseAdapter):
    """
    Queries data.europa.eu SPARQL-backed search API for CSV datasets.
    """
    name     = "dataeuropa"
    BASE_URL = "https://data.europa.eu/api/hub/search/search"

    async def search(self, query, domain, client, limit=10):
        params = {
            "q":       query,
            "filter":  "format=CSV",
            "limit":   limit,
            "page":    1,
        }
        try:
            resp = await robust_get(client, self.BASE_URL, params=params)
            data = resp.json()
        except Exception as exc:
            log.warning("[dataeuropa] Search failed: %s", exc)
            return []

        candidates = []
        for item in data.get("result", {}).get("results", [])[:limit]:
            try:
                title = item.get("title", {})
                title = title.get("en", list(title.values())[0]) if isinstance(title, dict) else str(title)
                for dist in item.get("distributions", []):
                    fmt = dist.get("format", {}).get("label", "").upper()
                    if fmt != "CSV":
                        continue
                    url = dist.get("downloadURL") or dist.get("accessURL", "")
                    if url:
                        candidates.append(DatasetCandidate(
                            title          = title,
                            url            = url,
                            domain         = domain,
                            source_adapter = self.name,
                            description    = item.get("description", {}).get("en", "")[:300],
                            file_format    = "csv",
                        ))
                        break
            except Exception:
                continue
        return candidates


# ── 5. OPENDATASOFT ───────────────────────────────────────────────────────────

class OpenDataSoftAdapter(BaseAdapter):
    """
    Searches public OpenDataSoft portals (used by hundreds of cities/agencies).
    Queries the global explore API endpoint.
    """
    name     = "opendatasoft"
    BASE_URL = "https://data.opendatasoft.com/api/explore/v2.1/catalog/datasets"

    async def search(self, query, domain, client, limit=10):
        params = {
            "where":   query,
            "limit":   limit,
            "order_by": "explore_count desc",
        }
        try:
            resp = await robust_get(client, self.BASE_URL, params=params)
            data = resp.json()
        except Exception as exc:
            log.warning("[opendatasoft] Search failed: %s", exc)
            return []

        candidates = []
        for item in data.get("results", [])[:limit]:
            try:
                ds_id  = item.get("dataset_id", "")
                title  = item.get("metas", {}).get("default", {}).get("title", ds_id)
                desc   = item.get("metas", {}).get("default", {}).get("description", "")
                # Build direct CSV export URL
                csv_url = (
                    f"https://data.opendatasoft.com/api/explore/v2.1/catalog/"
                    f"datasets/{ds_id}/exports/csv?limit=-1&delimiter=%3B"
                )
                records = item.get("metas", {}).get("default", {}).get("records_count", 0)
                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = csv_url,
                    domain         = domain,
                    source_adapter = self.name,
                    description    = (desc or "")[:300],
                    file_format    = "csv",
                    estimated_rows = int(records or 0),
                ))
            except Exception:
                continue
        return candidates


# ── 6. GITHUB CSV SEARCH ──────────────────────────────────────────────────────

class GithubSearchAdapter(BaseAdapter):
    """
    Searches GitHub for CSV files using the code search API.
    Finds raw CSV files hosted on GitHub across public repos.
    No auth required for basic use (60 req/hr unauthenticated).
    """
    name     = "github"
    BASE_URL = "https://api.github.com/search/code"

    async def search(self, query, domain, client, limit=10):
        params = {
            "q":        f"{query} extension:csv",
            "per_page": limit,
            "sort":     "indexed",
        }
        headers = {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Add token if available in env
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = await robust_get(client, self.BASE_URL, params=params, headers=headers)
            data = resp.json()
        except Exception as exc:
            log.warning("[github] Search failed for '%s': %s", query, exc)
            return []

        candidates = []
        for item in data.get("items", [])[:limit]:
            try:
                repo    = item["repository"]["full_name"]
                path    = item["path"]
                branch  = item["repository"].get("default_branch", "main")
                raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
                title   = f"{repo}/{Path(path).name}"
                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = raw_url,
                    domain         = domain,
                    source_adapter = self.name,
                    description    = item["repository"].get("description", ""),
                    file_format    = "csv",
                    meta           = {"repo": repo, "path": path},
                ))
            except Exception:
                continue
        return candidates


# ── 7. DUCKDUCKGO WEB SEARCH → CSV LINKS ─────────────────────────────────────

class DDGSearchAdapter(BaseAdapter):
    """
    Uses DuckDuckGo search to find CSV files across the open web.
    Searches for filetype:csv + query, then filters results to direct CSV links.
    Falls back to parsing search result snippets for CSV URLs.
    """
    name = "ddg"

    async def search(self, query, domain, client, limit=10):
        # Try duckduckgo-search library first (faster)
        candidates = await self._search_ddgs(query, domain, limit)
        if not candidates:
            candidates = await self._search_html(query, domain, client, limit)
        return candidates

    async def _search_ddgs(self, query, domain, limit):
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
        candidates = []
        search_q   = f"{query} filetype:csv"
        try:
            loop    = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: list(DDGS().text(search_q, max_results=limit * 2))
            )
            for r in results:
                url   = r.get("href", "")
                title = r.get("title", url)
                body  = r.get("body", "")
                # Only keep direct CSV links or pages that mention CSV
                if url.endswith(".csv") or ".csv" in url.lower():
                    fmt = "csv"
                elif url.endswith(".zip"):
                    fmt = "zip"
                else:
                    fmt = "html_table"
                candidates.append(DatasetCandidate(
                    title          = title,
                    url            = url,
                    domain         = domain,
                    source_adapter = self.name,
                    description    = body[:300],
                    file_format    = fmt,
                ))
                if len(candidates) >= limit:
                    break
        except Exception as exc:
            log.warning("[ddg] DDGS search failed: %s", exc)
        return candidates

    async def _search_html(self, query, domain, client, limit):
        """Fallback: scrape DuckDuckGo HTML results."""
        url    = "https://html.duckduckgo.com/html/"
        params = {"q": f"{query} filetype:csv", "kl": "us-en"}
        try:
            resp = await throttled_get(client, url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            log.warning("[ddg] HTML fallback failed: %s", exc)
            return []

        candidates = []
        for result in soup.select(".result")[:limit * 2]:
            link_el = result.select_one(".result__url, a[href]")
            if not link_el:
                continue
            href = link_el.get("href", link_el.get_text(strip=True))
            # DuckDuckGo wraps links — extract real URL
            if "uddg=" in href:
                from urllib.parse import unquote, parse_qs
                qs   = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [href])[0])
            if not href.startswith("http"):
                continue
            title_el = result.select_one(".result__title, .result__a")
            title    = title_el.get_text(strip=True) if title_el else href
            fmt      = "csv" if ".csv" in href.lower() else "html_table"
            candidates.append(DatasetCandidate(
                title=title, url=href, domain=domain,
                source_adapter=self.name, file_format=fmt,
            ))
            if len(candidates) >= limit:
                break
        return candidates


# ── 8. GOOGLE DATASET SEARCH ──────────────────────────────────────────────────

class GoogleDatasetAdapter(BaseAdapter):
    """
    Scrapes Google Dataset Search (datasetsearch.research.google.com).
    Extracts dataset landing pages, then probes each for a downloadable CSV.
    """
    name     = "google_dataset"
    BASE_URL = "https://datasetsearch.research.google.com/search"

    async def search(self, query, domain, client, limit=10):
        params = {"query": query, "docid": ""}
        try:
            resp = await robust_get(client, self.BASE_URL, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            log.warning("[google_dataset] Fetch failed: %s", exc)
            return []

        candidates = []
        # Google Dataset Search embeds JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if not isinstance(data, dict):
                    continue
                if data.get("@type") not in ("Dataset", "DataCatalog"):
                    continue
                title = data.get("name", "")
                desc  = data.get("description", "")[:300]
                # Look for CSV distribution
                for dist in data.get("distribution", []):
                    enc_fmt = dist.get("encodingFormat", "").lower()
                    url     = dist.get("contentUrl") or dist.get("url", "")
                    if "csv" in enc_fmt or url.endswith(".csv"):
                        candidates.append(DatasetCandidate(
                            title=title, url=url, domain=domain,
                            source_adapter=self.name, description=desc,
                            file_format="csv",
                        ))
                        break
                if len(candidates) >= limit:
                    break
            except Exception:
                continue

        # Fallback: grab card links if JSON-LD parsing yields nothing
        if not candidates:
            for card in soup.select("div[data-docid], article, .dataset-card")[:limit]:
                link = card.find("a", href=True)
                if not link:
                    continue
                href  = link["href"]
                title = link.get_text(strip=True) or href
                candidates.append(DatasetCandidate(
                    title=title, url=href, domain=domain,
                    source_adapter=self.name, file_format="html_table",
                ))

        return candidates


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD ENGINE
# ─────────────────────────────────────────────────────────────────────────────

async def download_candidate(
    candidate: DatasetCandidate,
    client: httpx.AsyncClient,
    kaggle_adapter: Optional[KaggleSearchAdapter] = None,
) -> Optional[AcquiredDataset]:
    """
    Download a candidate dataset and return an AcquiredDataset, or None on failure.
    Handles: direct CSV, ZIP archives, JSON APIs, HTML tables, Kaggle refs.
    """
    if CFG.dry_run:
        log.info("[dry-run] Would download: %s", candidate.slug)
        return None

    start = time.monotonic()
    fmt   = candidate.file_format
    path  = output_path(candidate.domain, candidate.slug)

    # ── Kaggle ref ────────────────────────────────────────────────────────────
    if fmt == "kaggle_ref" and kaggle_adapter:
        loop = asyncio.get_event_loop()
        dfs  = await loop.run_in_executor(None, kaggle_adapter.download_sync, candidate)
        if not dfs:
            return None
        df = pd.concat(dfs, ignore_index=True)
        return _finalise(df, candidate, path, start)

    if not can_fetch(candidate.url):
        log.warning("[%s] Blocked by robots.txt", candidate.slug)
        return None

    # Check file size via HEAD before downloading
    try:
        head = await throttled_get(client, candidate.url, headers={"Range": "bytes=0-0"})
        content_len = int(head.headers.get("content-length", 0))
        max_bytes   = CFG.max_file_mb * 1024 * 1024
        if content_len > max_bytes:
            log.warning("[%s] Skipping — file too large (%.1f MB)", candidate.slug,
                        content_len / 1024 / 1024)
            return None
    except Exception:
        pass  # HEAD not supported — proceed anyway

    try:
        resp = await robust_get(client, candidate.url)
    except Exception as exc:
        log.error("[%s] Download failed: %s", candidate.slug, exc)
        return None

    content_type = resp.headers.get("content-type", "").lower()
    raw          = resp.content

    # ── ZIP archive ───────────────────────────────────────────────────────────
    if "zip" in content_type or candidate.url.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                csv_name = next(
                    (n for n in zf.namelist() if n.endswith(".csv")), None
                )
                if not csv_name:
                    log.warning("[%s] No CSV inside ZIP.", candidate.slug)
                    return None
                raw = zf.read(csv_name)
        except Exception as exc:
            log.error("[%s] ZIP extraction failed: %s", candidate.slug, exc)
            return None
        fmt = "csv"

    # ── JSON → flatten ────────────────────────────────────────────────────────
    if "json" in content_type or candidate.url.endswith(".json"):
        try:
            data = resp.json()
            if isinstance(data, list):
                df = pd.json_normalize(data)
            elif isinstance(data, dict):
                # Try common wrapper keys
                for key in ("data", "results", "records", "items", "value"):
                    if key in data and isinstance(data[key], list):
                        df = pd.json_normalize(data[key])
                        break
                else:
                    df = pd.json_normalize([data])
            return _finalise(df, candidate, path, start)
        except Exception as exc:
            log.error("[%s] JSON parse failed: %s", candidate.slug, exc)
            return None

    # ── HTML table ────────────────────────────────────────────────────────────
    if fmt == "html_table" or "html" in content_type:
        html = resp.text
        # Try Playwright if httpx got a near-empty page (JS-rendered)
        if html.count("<table") == 0:
            html = await _playwright_fetch(candidate.url) or html
        try:
            tables = pd.read_html(html)
            if not tables:
                return None
            df = tables[0]
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [" ".join(str(c) for c in col).strip() for col in df.columns]
            return _finalise(df, candidate, path, start)
        except Exception as exc:
            # Maybe the page has a direct CSV link — probe it
            soup     = BeautifulSoup(html, "html.parser")
            csv_link = soup.find("a", href=re.compile(r"\.csv", re.I))
            if csv_link:
                href = csv_link["href"]
                if not href.startswith("http"):
                    base = f"{urlparse(candidate.url).scheme}://{urlparse(candidate.url).netloc}"
                    href = base + href
                try:
                    r2  = await robust_get(client, href)
                    raw = r2.content
                    # fall through to CSV parse below
                except Exception:
                    pass
            else:
                log.warning("[%s] No tables found: %s", candidate.slug, exc)
                return None

    # ── CSV (default) ─────────────────────────────────────────────────────────
    try:
        df = pd.read_csv(
            io.BytesIO(raw),
            low_memory=False,
            on_bad_lines="skip",
            encoding_errors="replace",
        )
        return _finalise(df, candidate, path, start)
    except Exception as exc:
        log.error("[%s] CSV parse failed: %s", candidate.slug, exc)
        return None


def _finalise(
    df: pd.DataFrame,
    candidate: DatasetCandidate,
    path: Path,
    start: float,
) -> Optional[AcquiredDataset]:
    if df is None or df.empty or len(df) < CFG.min_rows:
        log.warning("[%s] Too few rows (%d) — skipped.", candidate.slug,
                    len(df) if df is not None else 0)
        return None
    df = clean_df(df, candidate)
    df.to_csv(path, index=False)
    elapsed = round(time.monotonic() - start, 2)
    log.info("[%s] ✓  %d rows × %d cols  %.1fs  → %s",
             candidate.slug, len(df), len(df.columns), elapsed, path)
    return AcquiredDataset(
        candidate = candidate,
        path      = path,
        rows      = len(df),
        columns   = len(df.columns),
        elapsed_s = elapsed,
    )


async def _playwright_fetch(url: str) -> Optional[str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page    = await browser.new_page(user_agent=CFG.user_agent)
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            html    = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        log.debug("Playwright failed for %s: %s", url, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MANIFEST
# ─────────────────────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    p = Path(CFG.manifest_file)
    return json.loads(p.read_text()) if p.exists() else {}


def save_manifest(manifest: dict) -> None:
    Path(CFG.manifest_file).parent.mkdir(parents=True, exist_ok=True)
    Path(CFG.manifest_file).write_text(json.dumps(manifest, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

ALL_ADAPTERS: list[BaseAdapter] = [
    KaggleSearchAdapter(),
    UCISearchAdapter(),
    DataGovAdapter(),
    DataEuropaAdapter(),
    OpenDataSoftAdapter(),
    GithubSearchAdapter(),
    DDGSearchAdapter(),
    GoogleDatasetAdapter(),
]


async def discover_domain(
    domain: str,
    queries: list[str],
    adapters: list[BaseAdapter],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    per_domain: int,
) -> list[DatasetCandidate]:
    """Run all adapters × queries for one domain, deduplicate, rank, return top-N."""
    all_candidates: list[DatasetCandidate] = []

    async def _query(adapter: BaseAdapter, q: str):
        async with semaphore:
            try:
                return await adapter.search(q, domain, client, limit=per_domain)
            except Exception as exc:
                log.warning("[%s/%s] Search error: %s", adapter.name, domain, exc)
                return []

    tasks   = [_query(a, q) for a in adapters for q in queries[:2]]  # 2 queries per adapter
    results = await asyncio.gather(*tasks)

    for batch in results:
        all_candidates.extend(batch)

    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[DatasetCandidate] = []
    for c in all_candidates:
        if c.url not in seen:
            seen.add(c.url)
            unique.append(c)

    # Score and rank
    for c in unique:
        c.score = score_candidate(c)
    unique.sort(key=lambda c: c.score, reverse=True)

    top = unique[:per_domain * len(adapters)]
    log.info("[discover] %s → %d candidates (from %d raw)", domain, len(top), len(all_candidates))
    return top


async def run_pipeline(
    domains: list[str],
    adapters: list[BaseAdapter],
    per_domain: int,
) -> None:
    semaphore   = asyncio.Semaphore(CFG.max_concurrency)
    manifest    = load_manifest()
    kaggle_adp  = next((a for a in adapters if isinstance(a, KaggleSearchAdapter)), None)
    total_rows  = 0
    total_saved = 0
    errors      = 0

    headers = {
        "User-Agent":      CFG.user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept":          "text/html,application/xhtml+xml,application/json,text/csv,*/*",
    }

    async with httpx.AsyncClient(
        timeout  = CFG.request_timeout,
        headers  = headers,
        follow_redirects = True,
    ) as client:

        for domain in domains:
            queries    = DOMAIN_QUERIES.get(domain, [domain])
            candidates = await discover_domain(
                domain, queries, adapters, client, semaphore, per_domain
            )

            if CFG.dry_run:
                log.info("[dry-run] %s: %d candidates discovered (no downloads)", domain, len(candidates))
                for c in candidates:
                    log.info("  [%s] %.0f pts  %s  %s", c.source_adapter, c.score, c.file_format, c.title[:70])
                continue

            # Download candidates for this domain concurrently
            async def _download(c: DatasetCandidate):
                async with semaphore:
                    return await download_candidate(c, client, kaggle_adp)

            acquired = await asyncio.gather(*[_download(c) for c in candidates])

            for result in acquired:
                if result is None:
                    errors += 1
                    continue
                total_rows  += result.rows
                total_saved += 1
                entry = {
                    **asdict(result.candidate),
                    "path":      str(result.path),
                    "rows":      result.rows,
                    "columns":   result.columns,
                    "elapsed_s": result.elapsed_s,
                    "status":    result.status,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                manifest[result.candidate.slug] = entry

    save_manifest(manifest)
    log.info("═" * 60)
    log.info("Complete. %d files saved | %d total rows | %d skipped/failed",
             total_saved, total_rows, errors)
    log.info("Manifest → %s", CFG.manifest_file)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous multi-domain dataset discovery & acquisition"
    )
    parser.add_argument(
        "--domains", nargs="+", default=list(DOMAIN_QUERIES.keys()),
        help="Domains to search (default: all)",
    )
    parser.add_argument(
        "--adapters", nargs="+",
        choices=[a.name for a in ALL_ADAPTERS],
        help="Adapters to use (default: all)",
    )
    parser.add_argument(
        "--per-domain", type=int, default=CFG.per_domain, dest="per_domain",
        help=f"Target datasets per domain (default: {CFG.per_domain})",
    )
    parser.add_argument(
        "--workers", type=int, default=CFG.max_concurrency,
        help=f"Max concurrent HTTP requests (default: {CFG.max_concurrency})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover candidates only, no downloads",
    )
    parser.add_argument(
        "--output-dir", default=CFG.output_dir,
        help=f"Root output directory (default: {CFG.output_dir})",
    )
    parser.add_argument(
        "--list-domains", action="store_true",
        help="Print available domains and exit",
    )

    args = parser.parse_args()

    if args.list_domains:
        print("\nAvailable domains:")
        for d, qs in DOMAIN_QUERIES.items():
            print(f"  {d:<15} {qs[0]}")
        return

    CFG.max_concurrency = args.workers
    CFG.dry_run         = args.dry_run
    CFG.output_dir      = args.output_dir
    CFG.per_domain      = args.per_domain

    adapters = ALL_ADAPTERS
    if args.adapters:
        adapters = [a for a in ALL_ADAPTERS if a.name in args.adapters]

    log.info("Domains  : %s", ", ".join(args.domains))
    log.info("Adapters : %s", ", ".join(a.name for a in adapters))
    log.info("Per domain: %d  |  Workers: %d  |  Dry-run: %s",
             CFG.per_domain, CFG.max_concurrency, CFG.dry_run)

    asyncio.run(run_pipeline(args.domains, adapters, CFG.per_domain))


if __name__ == "__main__":
    main()