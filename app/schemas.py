from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchStats(BaseModel):
    model_config = ConfigDict(extra="ignore")
    comment_count: int = 0
    digg_count: int = 0
    play_count: int = 0
    share_count: int = 0


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    desc: str = ""
    content_type: str = ""
    create_time: Any = ""  # API varia str/int (unix ts) por endpoint
    region: str = ""
    url: str = ""
    statistics: SearchStats = Field(default_factory=SearchStats)
    author: dict[str, Any] = Field(default_factory=dict)
    market: str = ""
    sinal_esperado: str = ""

    @property
    def author_id(self) -> str:
        return str(self.author.get("unique_id") or self.author.get("uid") or self.id)

    @property
    def author_nick(self) -> str:
        return str(self.author.get("nickname") or self.author.get("unique_id") or "")

    def ct_int(self) -> Any:
        try:
            return int(self.create_time)
        except (TypeError, ValueError):
            return None


class CommentSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    cid: str = ""
    digg_count: int = 0
    reply_comment_total: int = 0
    create_time: Any = ""
    user: dict[str, Any] = Field(default_factory=dict)


def hashtag_to_item(a: dict[str, Any]) -> SearchItem:
    """Normaliza item de /search/hashtag (aweme_list): aweme_id/share_url."""
    return SearchItem(
        id=str(a.get("aweme_id", "")),
        desc=a.get("desc", "") or "",
        content_type=a.get("content_type", "") or "",
        create_time=a.get("create_time", ""),
        region=a.get("region", "") or "",
        url=a.get("share_url", "") or "",
        statistics=SearchStats.model_validate(a.get("statistics", {}) or {}),
        author=a.get("author", {}) or {},
    )
