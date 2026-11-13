"""Web Search Skill v3 — 缓存 + 动态过滤 + 搜索抓取联动

优化:
  1. TTL 缓存 — 5分钟内重复查询直接返回，避免重复 API 调用
  2. 动态过滤 — 去重、截断、按相关性排序，减少无效 token
  3. 搜索+抓取联动 — search_and_fetch 一步完成搜索→抓取→总结
"""

from __future__ import annotations
import asyncio, hashlib, html, json, os, re, time, urllib.parse, urllib.request
from typing import Any
from .base import Skill, SkillContract

# ═══════════════════════════════════════════════════════════
# Search Cache
# ═══════════════════════════════════════════════════════════

class SearchCache:
    """带 TTL 的搜索缓存。"""
    def __init__(self, ttl: int = 300, max_size: int = 100):
        self._cache: dict[str, tuple[float, dict]] = {}
        self._ttl = ttl
        self._max = max_size

    def _key(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]

    def get(self, query: str) -> dict | None:
        key = self._key(query)
        entry = self._cache.get(key)
        if entry:
            ts, result = entry
            if time.time() - ts < self._ttl:
                result["_cached"] = True
                return result
            del self._cache[key]
        return None

    def set(self, query: str, result: dict):
        key = self._key(query)
        if len(self._cache) >= self._max:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.time(), dict(result))

    def clear(self):
        self._cache.clear()

    def stats(self) -> dict:
        return {"size": len(self._cache), "ttl": self._ttl, "max": self._max}


# 全局缓存实例
_search_cache = SearchCache(ttl=300, max_size=100)


# ═══════════════════════════════════════════════════════════
# Dynamic Filter
# ═══════════════════════════════════════════════════════════

def dynamic_filter(results: list[dict], max_results: int = 5, max_content_len: int = 150) -> list[dict]:
    """过滤搜索结果：去重 → 截断 → 排序。纯代码，不消耗 token。"""
    seen_urls = set()
    seen_titles = set()
    filtered = []

    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        content = r.get("content", "")

        # 去重
        if url and url in seen_urls: continue
        if title and title[:30] in seen_titles: continue
        if url: seen_urls.add(url)
        if title: seen_titles.add(title[:30])

        # 截断内容
        if len(content) > max_content_len:
            content = content[:max_content_len] + "..."

        filtered.append({**r, "content": content})

        if len(filtered) >= max_results:
            break

    # 按 score 排序
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
    return filtered


# ═══════════════════════════════════════════════════════════
# WebSearch Skill
# ═══════════════════════════════════════════════════════════

WEBSEARCH_SCHEMA = {
    "name": "web_search",
    "description": "搜索互联网。自动缓存5分钟；按设置顺序尝试搜索源，失败时使用 Bing。",
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索词"},
        "max_results": {"type": "integer", "description": "最大结果数，默认5"},
    }, "required": ["query"]},
}

WEBSEARCH_FETCH_SCHEMA = {
    "name": "search_and_fetch",
    "description": "搜索并自动抓取前N条结果的全文，生成摘要。一步完成搜索→抓取→总结。",
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索词"},
        "max_results": {"type": "integer", "description": "搜索结果数，默认3"},
        "fetch_count": {"type": "integer", "description": "抓取前几条的全文，默认2"},
    }, "required": ["query"]},
}


class WebSearchSkill(Skill):
    PROVIDER_TIMEOUT = 8.0

    def __init__(self):
        super().__init__(SkillContract(
            name="web_search",
            capabilities=["web_search", "search", "internet", "search_and_fetch"],
            requires=["query"], produces=["results", "query"],
            cost_score=0.3, latency_score=0.4, quality_score=0.92,
        ))

    def to_tool_schema(self) -> dict: return WEBSEARCH_SCHEMA

    def to_tool_schemas(self) -> list[dict]:
        return [WEBSEARCH_SCHEMA, WEBSEARCH_FETCH_SCHEMA]

    # ── Main Execute ──────────────────────────

    async def execute(self, **kw) -> dict:
        """web_search 入口。"""
        query=str(kw.get("query", "")).strip()
        if not query:return {"results":[],"query":"","provider":"empty","error":"query 不能为空"}
        try:max_results=max(1,min(10,int(kw.get("max_results",5))))
        except (TypeError,ValueError):max_results=5
        return await self._search(query,max_results)

    async def execute_fetch(self, **kw) -> dict:
        """search_and_fetch 入口。"""
        query = str(kw.get("query", "")).strip()
        if not query:return {"results":[],"fetched":[],"summary":"","query":"","provider":"empty","error":"query 不能为空"}
        try:max_r=max(1,min(10,int(kw.get("max_results",3))))
        except (TypeError,ValueError):max_r=3
        try:fetch_n=max(0,min(max_r,int(kw.get("fetch_count",2))))
        except (TypeError,ValueError):fetch_n=min(2,max_r)

        # 先搜索
        search_result = await self._search(query, max_r)
        if search_result.get("error"):
            return {"results": [], "fetched": [], "summary": "", "query": query,
                    "provider": search_result.get("provider", "none"), "error": search_result["error"]}
        results = search_result.get("results", [])

        # 再抓取前 N 条
        async def fetch_one(r):
            url = r.get("url", "")
            if not url or not url.startswith("http"):
                return None
            try:
                content = await self._fetch_url(url)
                if not content.strip():
                    raise ValueError("页面没有返回可用正文")
                return {"url": url, "title": r.get("title", ""),
                        "content": content[:2000], "fetched": True}
            except Exception:
                return {"url": url, "title": r.get("title", ""),
                        "content": r.get("content", ""), "fetched": False}
        fetched = [page for page in await asyncio.gather(
            *(fetch_one(r) for r in results[:fetch_n])
        ) if page is not None]

        # 生成摘要
        summary = self._build_summary(query, fetched)

        return {
            "results": search_result.get("results", []),
            "fetched": fetched,
            "summary": summary,
            "query": query,
            "provider": search_result.get("provider", "unknown"),
        }

    # ── Core Search ──────────────────────────

    async def _search(self, query: str, max_r: int) -> dict:
        if not query:
            return {"results": [], "query": "", "provider": "empty"}

        # 1. 检查缓存
        cached = _search_cache.get(query)
        if cached:
            return cached

        # 2. 执行搜索
        from ..settings import get_settings
        settings = get_settings()
        order = settings.search_fallback_order
        result = None
        for provider in order:
            try:
                result = await asyncio.wait_for(
                    self._try_provider(provider, query, max_r, settings),
                    timeout=self.PROVIDER_TIMEOUT,
                )
            except (asyncio.TimeoutError, OSError):
                result = None
            if result and result.get("results"): break

        if not result or not result.get("results"):
            try:
                result = await asyncio.wait_for(
                    self._try_provider("bing", query, max_r, settings),
                    timeout=self.PROVIDER_TIMEOUT,
                )
            except (asyncio.TimeoutError, OSError):
                result = None

        if not result or not result.get("results"):
            return {"results": [], "query": query, "provider": "none", "error": "实时搜索暂不可用：百度、Bing 均未返回结果；请检查网络，或在设置中配置 Brave/Tavily 搜索 API Key。"}

        # 3. 动态过滤
        result["results"] = dynamic_filter(result.get("results", []), max_r)
        result["_filtered"] = True

        # 4. 写入缓存
        _search_cache.set(query, result)
        return result

    # ── Providers ────────────────────────────

    def _is_chinese(self, text: str) -> bool:
        cn = sum(1 for c in text if '一' <= c <= '鿿')
        return cn > len(text) * 0.25

    async def _try_provider(self, provider: str, query: str, max_r: int, settings) -> dict | None:
        if provider == "baidu":
            return await self._baidu(query, max_r)
        elif provider == "bing":
            return await self._bing(query, max_r)
        elif provider == "brave":
            return await self._brave(query, max_r, settings)
        elif provider == "tavily":
            return await self._tavily(query, max_r, settings)
        return None

    async def _bing(self, query: str, max_r: int) -> dict | None:
        """Use Bing's public results when configured providers are unavailable."""
        def fetch() -> str:
            url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&count={max_r}"
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.read(1_000_000).decode("utf-8", errors="replace")
        try:
            page = await asyncio.to_thread(fetch)
            results = []
            for block in re.findall(r'<li\s+class="b_algo"[^>]*>(.*?)(?=<li\s+class="b_algo"|</ol>)', page, re.DOTALL):
                heading = re.search(r'<h2[^>]*>\s*<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if not heading:
                    continue
                url = html.unescape(heading.group(1))
                title = html.unescape(re.sub(r'<[^>]+>', '', heading.group(2))).strip()
                caption = re.search(r'<div\s+class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL)
                snippet = html.unescape(re.sub(r'<[^>]+>', '', caption.group(1))).strip() if caption else title
                if title:
                    results.append({"title": title[:160], "url": url, "content": snippet[:500], "score": 0.8})
                if len(results) >= max_r:
                    break
            if results:
                return {"results": results, "query": query, "provider": "bing"}
        except Exception:
            pass
        return None

    async def _baidu(self, query: str, max_r: int) -> dict | None:
        try:
            url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&rn={max_r}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://www.baidu.com/",
            })
            html = (await asyncio.to_thread(self._read_request, req, 3)).decode("utf-8", errors="replace")
            results = []
            for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL):
                title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if not title or len(title) < 3: continue
                end = m.end()
                chunk = html[end:end+2000]
                snippet = ""
                for pat in [r'class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</',
                           r'<span[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</span>']:
                    sm = re.search(pat, chunk, re.DOTALL)
                    if sm:
                        snippet = re.sub(r'<[^>]+>', '', sm.group(1)).strip()
                        if len(snippet) > 5: break
                results.append({"title": title[:100], "url": m.group(1)[:200],
                               "content": snippet or title, "score": 0.85})
                if len(results) >= max_r * 2: break
            if results:
                return {"results": results, "query": query, "provider": "baidu"}
        except Exception: pass
        return None

    async def _brave(self, query: str, max_r: int, settings) -> dict | None:
        key = settings.get_search_key("brave") or os.environ.get("BRAVE_API_KEY", "")
        if not key: return None
        try:
            url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={max_r}"
            req = urllib.request.Request(url, headers={
                "Accept": "application/json", "Accept-Encoding": "gzip",
                "X-Subscription-Token": key,
            })
            data = json.loads((await asyncio.to_thread(self._read_request, req, 4)).decode())
            results = [{"title": r.get("title", ""), "url": r.get("url", ""),
                       "content": r.get("description", ""), "score": 1.0}
                       for r in data.get("web", {}).get("results", [])]
            if results:
                return {"results": results, "query": query, "provider": "brave"}
        except Exception: pass
        return None

    async def _tavily(self, query: str, max_r: int, settings) -> dict | None:
        key = settings.get_search_key("tavily") or os.environ.get("TAVILY_API_KEY", "")
        if not key: return None
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=key)
            result = await asyncio.to_thread(client.search, query, max_results=max_r, include_answer=True)
            return {"results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                    "content": r.get("content", ""), "score": r.get("score", 0)}
                   for r in result.get("results", [])], "query": query,
                    "answer": result.get("answer", ""), "provider": "tavily"}
        except Exception: pass
        return None

    @staticmethod
    def _read_request(req: urllib.request.Request, timeout: float) -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(1_000_000)

    # ── URL Fetch ────────────────────────────

    async def _fetch_url(self, url: str) -> str:
        """抓取 URL 全文。先 Jina，再直连。"""
        def fetch() -> str:
            try:
                req = urllib.request.Request(f"https://r.jina.ai/{url}", headers={
                    "User-Agent": "Mozilla/5.0", "Accept": "text/markdown",
                })
                with urllib.request.urlopen(req, timeout=8) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
                if content and len(content) > 100:
                    return content[:3000]
            except Exception:
                pass
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
                text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                return re.sub(r'\s+', ' ', text).strip()[:3000]
            except Exception:
                return ""
        return await asyncio.to_thread(fetch)

    def _build_summary(self, query: str, fetched: list) -> str:
        """根据抓取内容生成简要摘要。"""
        parts = [f"搜索 '{query}' 结果摘要:"]
        for i, f in enumerate(fetched):
            status = "已抓取" if f.get("fetched") else "仅摘要"
            parts.append(f"\n[{i+1}] {f.get('title','无标题')} ({status})")
            content = f.get("content", "")[:200]
            if content: parts.append(f"    {content}")
        return "\n".join(parts)
