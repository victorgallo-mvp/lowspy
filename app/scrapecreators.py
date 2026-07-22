from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import FIXTURES
from .schemas import (
    CommentSchema,
    SearchItem,
    extract_cover,
    facebook_ad_to_item,
    hashtag_to_item,
)

BASE_URL = "https://api.scrapecreators.com"

# callback(endpoint, credits_remaining, params)
CostCB = Callable[[str, Optional[int], dict], None]


class RetryableHTTP(Exception):
    pass


class LiveClient:
    def __init__(self, api_key: str, on_call: CostCB) -> None:
        self._c = httpx.Client(base_url=BASE_URL, headers={"x-api-key": api_key}, timeout=30.0)
        self.on_call = on_call

    @retry(
        retry=retry_if_exception_type(RetryableHTTP),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        r = self._c.get(path, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableHTTP(f"{r.status_code} {path}")
        r.raise_for_status()
        return r.json()

    def search_hashtag(self, hashtag: str, cursor=None):
        params: dict = {"hashtag": hashtag}
        if cursor is not None:
            params["cursor"] = cursor
        data = self._get("/v1/tiktok/search/hashtag", params)
        self.on_call("search_hashtag", data.get("credits_remaining"),
                     {"hashtag": hashtag, "cursor": cursor})
        items = [hashtag_to_item(a) for a in data.get("aweme_list", [])]
        return items, data.get("cursor")  # (items, next_cursor)

    def search_top(self, query: str, cfg: dict, cursor=None):
        s = cfg["search"]
        params: dict = {
            "query": query,
            "publish_time": s["publish_time"],
            "sort_by": s["sort_by"],
            "region": s["region"],
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = self._get("/v1/tiktok/search/top", params)
        self.on_call("search_top", data.get("credits_remaining"), {"query": query, "cursor": cursor})
        items = []
        for it in data.get("items", []):
            si = SearchItem.model_validate(it)
            if not si.cover_url:
                si.cover_url = extract_cover(it)
            items.append(si)
        return items, data.get("cursor")  # (items, next_cursor)

    def video_comments(self, url: str) -> list[CommentSchema]:
        # trim=false: trim=true devolve só ~4 comentários-lixo e mata o gate.
        data = self._get("/v1/tiktok/video/comments", {"url": url, "trim": "false"})
        self.on_call("video_comments", data.get("credits_remaining"), {"url": url})
        return [CommentSchema.model_validate(c) for c in data.get("comments", [])]

    def video_info(self, url: str) -> dict:
        """Engenharia reversa: dados de UM vídeo específico por link (legenda,
        hashtags, estatísticas) — não tem endpoint 'por hashtag', é o vídeo em si."""
        data = self._get("/v2/tiktok/video", {"url": url})
        self.on_call("video_info", data.get("credits_remaining"), {"url": url})
        return data.get("aweme_detail", {}) or {}

    def search_facebook_ads(self, query: str, cfg: dict, cursor=None):
        m = cfg.get("meta_ads", {})
        params: dict = {
            "query": query,
            "country": m.get("country", "BR"),
            "media_type": m.get("media_type", "ALL"),
            "status": m.get("status", "ACTIVE"),
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = self._get("/v1/facebook/adLibrary/search/ads", params)
        self.on_call("search_facebook_ads", data.get("credits_remaining"),
                     {"query": query, "cursor": cursor})
        items = [facebook_ad_to_item(a) for a in data.get("searchResults", [])]
        return items, data.get("cursor")  # (items, next_cursor)

    def company_ads_count(self, page_id: str, cfg: dict) -> tuple[int, bool]:
        """Quantos anúncios ATIVOS aquela página tem — 1 request, só 1ª página (não
        pagina o catálogo inteiro, custaria 1 crédito por página de anúncio do anunciante).
        Retorna (contagem_dessa_página, tem_mais) — "tem_mais" avisa quando o número é
        piso, não total exato (a página tinha mais resultados que não buscamos)."""
        m = cfg.get("meta_ads", {})
        params = {"pageId": page_id, "country": m.get("country", "BR"), "status": "ACTIVE"}
        data = self._get("/v1/facebook/adLibrary/company/ads", params)
        self.on_call("company_ads_count", data.get("credits_remaining"), {"pageId": page_id})
        results = data.get("results", [])
        return len(results), bool(data.get("cursor"))

    def close(self) -> None:
        self._c.close()


class DryRunClient:
    """Fixtures locais (gasto zero). Simula credits_remaining caindo p/ exercitar custo."""

    def __init__(self, on_call: CostCB, fixtures: Optional[Path] = None) -> None:
        self.on_call = on_call
        d = fixtures or FIXTURES
        self._top = json.loads((d / "top_search.json").read_text("utf-8"))
        self._comments = json.loads((d / "comments.json").read_text("utf-8"))
        self._meta_ads = json.loads((d / "facebook_ads.json").read_text("utf-8"))
        self._video_info = json.loads((d / "video_info.json").read_text("utf-8"))
        self._credits = int(self._top.get("credits_remaining", 1000))

    def _spend(self, endpoint: str, params: dict) -> None:
        self._credits -= 1
        self.on_call(endpoint, self._credits, params)

    def _items(self) -> list[SearchItem]:
        import time
        now = int(time.time())
        items = [SearchItem.model_validate(it) for it in self._top.get("items", [])]
        for si in items:  # fixture "recente" p/ passar no filtro de recência
            si.create_time = now
        return items

    def search_hashtag(self, hashtag: str, cursor=None):
        self._spend("search_hashtag", {"hashtag": hashtag, "cursor": cursor})
        return self._items(), None  # dry-run: página única

    def search_top(self, query: str, cfg: dict, cursor=None):
        self._spend("search_top", {"query": query, "cursor": cursor})
        return self._items(), None  # dry-run: página única

    def video_comments(self, url: str) -> list[CommentSchema]:
        self._spend("video_comments", {"url": url})
        return [CommentSchema.model_validate(c) for c in self._comments.get("comments", [])]

    def video_info(self, url: str) -> dict:
        self._spend("video_info", {"url": url})
        return self._video_info.get("aweme_detail", {}) or {}

    def search_facebook_ads(self, query: str, cfg: dict, cursor=None):
        self._spend("search_facebook_ads", {"query": query, "cursor": cursor})
        items = [facebook_ad_to_item(a) for a in self._meta_ads.get("searchResults", [])]
        return items, None  # dry-run: página única

    def company_ads_count(self, page_id: str, cfg: dict) -> tuple[int, bool]:
        self._spend("company_ads_count", {"pageId": page_id})
        n = sum(1 for a in self._meta_ads.get("searchResults", []) if a.get("page_id") == page_id)
        return n, False

    def close(self) -> None:
        pass
