# test_crawl4ai_firecrawl_server.py
# pytest 기반 테스트 코드
#
# 실행:
#   pip install pytest requests
#   pytest -v test_crawl4ai_firecrawl_server.py
#
# 전제:
#   crawl4ai docker 실행중
#   http://localhost:11235

import os
import pytest

# 서버 파일명에 맞춰 수정하세요
# 예: crawl4ai_firecrawl_server.py
from crawl4ai_firecrawl_server import (
    firecrawl_scrape,
    firecrawl_batch,
    firecrawl_screenshot,
    firecrawl_extract,
)

BASE_URL = os.getenv("CRAWL4AI_URL", "http://localhost:11235")


# ---------------------------
# 공통
# ---------------------------

def test_server_import():
    assert BASE_URL.startswith("http")


# ---------------------------
# scrape 테스트
# ---------------------------

def test_firecrawl_scrape_basic():
    TEST_URL = "https://comp.wisereport.co.kr/company/c1010001.aspx?cmp_cd=005930"  # 삼성전자 기업현황 페이지
    # TEST_URL = "https://finance.naver.com/item/news.naver?code=005930"
    result = firecrawl_scrape(TEST_URL, formats=["markdown", "html"], onlyMainContent=True)

    assert result["success"] is True
    assert result["url"] == TEST_URL
    assert isinstance(result["markdown"], str)
    assert len(result["markdown"]) > 0
    print(f'markdown 길이: {len(result["markdown"])}')
    print(f'html 길이: {len(result["html"])}')

    with open("test_output.md", "w", encoding="utf-8") as f:
        f.write(result["markdown"])

    with open("test_output.html", "w", encoding="utf-8") as f:
        f.write(result["html"])


def test_firecrawl_scrape_html_exists():
    result = firecrawl_scrape("https://example.com", formats=["html"])

    assert "html" in result
    assert isinstance(result["html"], str)


# ---------------------------
# batch 테스트
# ---------------------------

def test_firecrawl_batch_multiple_urls():
    urls = [
        "https://example.com",
        "https://httpbin.org/html",
    ]

    result = firecrawl_batch(urls)

    assert result["success"] is True
    assert "results" in result
    assert len(result["results"]) >= 2


# ---------------------------
# screenshot 테스트
# ---------------------------

def test_firecrawl_screenshot():
    result = firecrawl_screenshot("https://example.com")

    assert result["success"] is True
    assert result["url"] == "https://example.com"

    # base64 이미지 문자열
    assert result["screenshot"] is not None
    assert isinstance(result["screenshot"], str)
    assert len(result["screenshot"]) > 100


# ---------------------------
# extract 테스트
# ---------------------------

def test_firecrawl_extract_title():
    schema = {
        "name": "Example Page",
        "baseSelector": "body",
        "fields": [
            {
                "name": "title",
                "selector": "h1",
                "type": "text"
            },
            {
                "name": "paragraph",
                "selector": "p",
                "type": "text"
            }
        ]
    }

    result = firecrawl_extract(
        "https://example.com",
        schema
    )

    assert result["success"] is True
    assert result["url"] == "https://example.com"
    assert "data" in result
    assert result["data"] is not None


# ---------------------------
# 예외 테스트
# ---------------------------

def test_invalid_url():
    with pytest.raises(Exception):
        firecrawl_scrape("not-a-url")


def test_unreachable_domain():
    with pytest.raises(Exception):
        firecrawl_scrape("https://this-domain-does-not-exist-123456.com")


# ---------------------------
# 성능 스모크 테스트
# ---------------------------

def test_scrape_response_fast():
    import time

    start = time.time()
    result = firecrawl_scrape("https://example.com")
    elapsed = time.time() - start

    assert result["success"] is True
    assert elapsed < 60