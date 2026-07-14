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
    cover_url: str = ""
    market: str = ""
    sinal_esperado: str = ""
    novo: bool = False  # transiente: 1ª vez visto (não estava no DB antes deste run)

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


def _first_url(v: Any) -> str:
    """TikTok expõe imagem como str, {url_list:[...]}, ou lista disso."""
    if not v:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        ul = v.get("url_list") or v.get("urlList")
        if ul:
            return ul[0]
        return v.get("url") or ""
    if isinstance(v, list) and v:
        return _first_url(v[0])
    return ""


def extract_cover(a: dict[str, Any]) -> str:
    """Melhor esforço p/ a capa: vídeo (cover/origin/dynamic) ou 1ª foto do carrossel."""
    video = a.get("video") or {}
    for key in ("cover", "origin_cover", "dynamic_cover"):
        u = _first_url(video.get(key))
        if u:
            return u
    imgs = a.get("images") or (a.get("image_post_info") or {}).get("images")
    u = _first_url(imgs)
    if u:
        return u
    return _first_url(a.get("cover"))


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
        cover_url=extract_cover(a),
    )
