from __future__ import annotations

import re
import urllib.parse
import logging
import requests

logger = logging.getLogger(__name__)

def search_baidu(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search Baidu and extract titles, snippets, and links without API keys."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "Referer": "https://www.baidu.com/"
    }
    q_encoded = urllib.parse.quote(query)
    url = f"https://www.baidu.com/s?wd={q_encoded}"
    results = []
    try:
        # Bypass proxies as Baidu often blocks proxy IPs
        res = requests.get(url, headers=headers, proxies={"http": None, "https": None}, timeout=10)
        res.raise_for_status()
        
        # Split HTML by H3 result headers
        pattern = re.compile(r'(<h3[^>]*class="[^"]*\bt\b[^"]*"[^>]*>.*?)(?=<h3 class="[^"]*\bt\b[^"]*"|$)', re.DOTALL)
        blocks = pattern.findall(res.text)
        
        for b in blocks[:limit]:
            # Extract link and title from H3 tag
            title_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h3>', b, re.DOTALL)
            if not title_match:
                continue
                
            link = title_match.group(1).strip()
            title_raw = title_match.group(2)
            title = re.sub(r'<.*?>', '', title_raw).strip()
            
            # Extract snippet by cleaning HTML tags
            snippet_raw = re.sub(r'<[^>]+>', ' ', b)
            snippet_clean = re.sub(r'\s+', ' ', snippet_raw).strip()
            
            # Remove any trailing JSON-like strings
            if "{" in snippet_clean:
                snippet_clean = snippet_clean.split("{")[0].strip()
                
            # If title is in snippet, remove it to avoid redundancy
            if snippet_clean.startswith(title):
                snippet_clean = snippet_clean[len(title):].strip()
                
            results.append({
                "title": title,
                "url": link,
                "snippet": snippet_clean[:200]
            })
    except Exception as e:
        logger.error(f"Baidu search failed for query '{query}': {e}")
        
    return results
