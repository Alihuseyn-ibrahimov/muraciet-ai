"""Rəsmi saytın FAQ/xidmət səhifələrini bilik bazasına çəkir (Web Scraping).

İstifadə:
    python -m scripts.scrape_knowledge https://example.gov.az/faq https://example.gov.az/xidmetler
    python -m app.rag.ingest

Qeyd: yalnız icazəniz olan saytları scrape edin və robots.txt qaydalarına əməl edin.
"""
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "scraped"
HEADERS = {"User-Agent": "MuracietAI-KnowledgeBot/1.0"}


def allowed(url: str) -> bool:
    p = urlparse(url)
    rp = RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True
    return rp.can_fetch(HEADERS["User-Agent"], url)


def page_to_markdown(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else url
    lines = [f"# {title}", f"Mənbə: {url}", ""]
    for el in root.find_all(["h1", "h2", "h3", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        prefix = {"h1": "## ", "h2": "## ", "h3": "### ", "li": "- "}.get(el.name, "")
        lines.append(prefix + text)
    return "\n".join(lines)


def main(urls: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for url in urls:
            if not allowed(url):
                print(f"robots.txt icazə vermir: {url}")
                continue
            resp = client.get(url)
            resp.raise_for_status()
            name = re.sub(r"[^\w]+", "_", urlparse(url).path).strip("_") or "index"
            (OUT_DIR / f"{urlparse(url).netloc}_{name}.md").write_text(page_to_markdown(resp.text, url), "utf-8")
            print(f"✓ {url}")
            time.sleep(1)  # serveri yükləməmək üçün


if __name__ == "__main__":
    main(sys.argv[1:])
