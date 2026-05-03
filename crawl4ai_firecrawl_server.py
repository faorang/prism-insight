# crawl4ai_firecrawl_server.py
# Firecrawl 호환 MCP 서버 (crawl4ai backend)
# v5 - onlyMainContent + 광고/배너 제거 강화판
#
# pip install mcp requests
# python crawl4ai_firecrawl_server.py

import os
import time
import requests

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("firecrawl")

BASE_URL = os.getenv("CRAWL4AI_URL", "http://localhost:11235")


# ---------------------------------------------------
# Utility
# ---------------------------------------------------

def _flatten_markdown(md):
    if isinstance(md, str):
        return md

    if isinstance(md, dict):
        return (
            md.get("raw_markdown")
            or md.get("markdown_with_citations")
            or md.get("fit_markdown")
            or ""
        )

    return ""


def _trim(text: str, max_chars: int):
    if not text:
        return ""

    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return text

    return text[:max_chars] + "\n\n...[truncated]"


def _sync(payload, timeout=180):
    r = requests.post(
        f"{BASE_URL}/crawl",
        json=payload,
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()


def _async(payload, timeout=300):
    r = requests.post(
        f"{BASE_URL}/crawl/job",
        json=payload,
        timeout=30
    )
    r.raise_for_status()

    task_id = r.json()["task_id"]
    start = time.time()

    while True:
        if time.time() - start > timeout:
            raise TimeoutError("crawl timeout")

        g = requests.get(
            f"{BASE_URL}/crawl/job/{task_id}",
            timeout=30
        )
        g.raise_for_status()

        data = g.json()

        if data["status"] == "completed":
            return data["result"]

        if data["status"] == "failed":
            raise RuntimeError(data.get("error", "crawl failed"))

        time.sleep(2)


# ---------------------------------------------------
# 광고 / 배너 / 추천영역 제거 JS
# ---------------------------------------------------

REMOVE_NOISE_JS = """
(() => {
  const selectors = [
    '.banner_smart',
  ];

  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => el.remove());
  });
})();
"""


# ---------------------------------------------------
# Params
# ---------------------------------------------------

def _build_params(only_main_content: bool):
    params = {
        "process_iframes": True,
        "remove_overlay_elements": True,
        "scan_full_page": True,
        "wait_for": "body"
    }

    if only_main_content:
        params.update({
            "js_code": [REMOVE_NOISE_JS]
        })

    return params


def _payload(urls, only_main_content=False):
    return {
        "urls": urls,
        "browser_config": {
            "headless": True
        },
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": _build_params(only_main_content)
        }
    }


# ---------------------------------------------------
# Tool: firecrawl_scrape
# ---------------------------------------------------

@mcp.tool()
def firecrawl_scrape(
    url: str,
    formats: list[str] = ["markdown"],
    onlyMainContent: bool = True,
    max_chars: int = 200_000
):
    """
    Firecrawl compatible scrape
    """

    payload = _payload([url], onlyMainContent)

    result = _sync(payload)

    row = result["results"][0]

    out = {
        "success": True,
        "url": url
    }

    if "markdown" in formats:
        md = _flatten_markdown(row.get("markdown"))
        out["markdown"] =  _trim(md, max_chars)

    if "html" in formats:
        html = row.get("html", "")
        out["html"] = _trim(html, max_chars)

    out["metadata"] = {
        "title": row.get("metadata", {}).get("title", "")
    }

    return out


# ---------------------------------------------------
# Tool: firecrawl_batch
# ---------------------------------------------------

@mcp.tool()
def firecrawl_batch(
    urls: list[str],
    onlyMainContent: bool = True,
    max_chars: int = 20_000
):
    """
    Batch scrape
    """

    payload = _payload(urls, onlyMainContent)

    result = _async(payload)

    rows = []

    for row in result["results"]:
        rows.append({
            "url": row.get("url"),
            "markdown": _trim(
                _flatten_markdown(row.get("markdown")),
                max_chars
            )
        })

    return {
        "success": True,
        "results": rows
    }


# ---------------------------------------------------
# Tool: firecrawl_extract
# ---------------------------------------------------

@mcp.tool()
def firecrawl_extract(
    url: str,
    schema: dict,
    onlyMainContent: bool = True
):
    """
    Structured extraction
    """

    payload = _payload([url], onlyMainContent)

    payload["crawler_config"]["params"]["extraction_strategy"] = {
        "type": "JsonCssExtractionStrategy",
        "params": {
            "schema": schema
        }
    }

    result = _async(payload)

    row = result["results"][0]

    return {
        "success": True,
        "url": url,
        "data": row.get("extracted_content", "")
    }


# ---------------------------------------------------
# Tool: firecrawl_screenshot
# ---------------------------------------------------

@mcp.tool()
def firecrawl_screenshot(url: str):
    """
    Screenshot capture
    """

    payload = _payload([url], False)
    payload["crawler_config"]["params"]["screenshot"] = True

    result = _async(payload)

    row = result["results"][0]

    return {
        "success": True,
        "url": url,
        "screenshot": row.get("screenshot", "")
    }


# ---------------------------------------------------
# Tool: health
# ---------------------------------------------------

@mcp.tool()
def firecrawl_health():
    r = requests.get(f"{BASE_URL}/health", timeout=10)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------

if __name__ == "__main__":
    mcp.run()