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
from .schemas import CommentSchema, SearchItem, extract_cover, hashtag_to_item

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

    def search_hashtag(self, hashtag: str) -> list[SearchItem]:
        data = self._get("/v1/tiktok/search/hashtag", {"hashtag": hashtag})
        self.on_call("search_hashtag", data.get("credits_remaining"), {"hashtag": hashtag})
        return [hashtag_to_item(a) for a in data.get("aweme_list", [])]

    def search_top(self, query: str, cfg: dict) -> list[SearchItem]:
        s = cfg["search"]
        params = {
            "query": query,
            "publish_time": s["publish_time"],
            "sort_by": s["sort_by"],
            "region": s["region"],
        }
        data = self._get("/v1/tiktok/search/top", params)
        self.on_call("search_top", data.get("credits_remaining"), {"query": query})
        items = []
        for it in data.get("items", []):
            si = SearchItem.model_validate(it)
            if not si.cover_url:
                si.cover_url = extract_cover(it)
            items.append(si)
        return items

    def video_comments(self, url: str) -> list[CommentSchema]:
        # trim=false: trim=true devolve só ~4 comentários-lixo e mata o gate.
        data = self._get("/v1/tiktok/video/comments", {"url": url, "trim": "false"})
        self.on_call("video_comments", data.get("credits_remaining"), {"url": url})
        return [CommentSchema.model_validate(c) for c in data.get("comments", [])]

    def close(self) -> None:
        self._c.close()


class DryRunClient:
    """Fixtures locais (gasto zero). Simula credits_remaining caindo p/ exercitar custo."""

    def __init__(self, on_call: CostCB, fixtures: Optional[Path] = None) -> None:
        self.on_call = on_call
        d = fixtures or FIXTURES
        self._top = json.loads((d / "top_search.json").read_text("utf-8"))
        self._comments = json.loads((d / "comments.json").read_text("utf-8"))
        self._credits = int(self._top.get("credits_remaining", 1000))

    def _spend(self, endpoint: str, params: dict) -> None:
        self._credits -= 1
        self.on_call(endpoint, self._credits, params)

    def search_hashtag(self, hashtag: str) -> list[SearchItem]:
        self._spend("search_hashtag", {"hashtag": hashtag})
        return [SearchItem.model_validate(it) for it in self._top.get("items", [])]

    def search_top(self, query: str, cfg: dict) -> list[SearchItem]:
        self._spend("search_top", {"query": query})
        return [SearchItem.model_validate(it) for it in self._top.get("items", [])]

    def video_comments(self, url: str) -> list[CommentSchema]:
        self._spend("video_comments", {"url": url})
        return [CommentSchema.model_validate(c) for c in self._comments.get("comments", [])]

    def close(self) -> None:
        pass
