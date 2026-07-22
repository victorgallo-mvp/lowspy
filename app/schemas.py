from __future__ import annotations

from typing import Any, Optional

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
    termo_origem: str = ""  # transiente: palavra-chave exata que achou este item
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


# --------------------------------------------------------------------------- #
# Meta Ads (Facebook Ad Library) — fonte separada, sem comentário. O sinal de
# demanda é o tempo de veiculação (anúncio que sobreviveu ao teste do mercado).
# --------------------------------------------------------------------------- #
class AdSnapshotBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""


class AdSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    body: AdSnapshotBody = Field(default_factory=AdSnapshotBody)
    title: str = ""  # separado do body — anúncio pode confirmar "é digital" só no título
    link_url: Optional[str] = None
    videos: list = Field(default_factory=list)
    images: list = Field(default_factory=list)
    cards: list = Field(default_factory=list)  # anúncio carrossel: mídia fica aqui, não em images/videos


class AdItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ad_archive_id: str = ""
    page_id: str = ""
    page_name: str = ""
    is_active: bool = False
    start_date: Any = None
    end_date: Any = None
    total_active_time: Any = 0
    collation_count: int = 0
    publisher_platform: list[str] = Field(default_factory=list)
    snapshot: AdSnapshot = Field(default_factory=AdSnapshot)
    market: str = ""
    sinal_esperado: str = ""
    termo_origem: str = ""  # transiente: palavra-chave exata que achou este item
    novo: bool = False  # transiente: 1ª vez visto (não estava no DB antes deste run)

    @property
    def id(self) -> str:
        return self.ad_archive_id

    @property
    def desc(self) -> str:
        """Título + corpo do anúncio, juntos — alguns só confirmam 'é digital'
        no título (ex: nome do produto), não no texto principal."""
        body = self.snapshot.body.text or ""
        if not body:
            # anúncio carrossel: sem body no topo, texto vem por card
            for card in self.snapshot.cards or []:
                if isinstance(card, dict) and card.get("body"):
                    body = card["body"]
                    break
        titulo = self.snapshot.title or ""
        return f"{titulo} {body}".strip()

    @property
    def url(self) -> str:
        return f"https://www.facebook.com/ads/library/?id={self.ad_archive_id}" if self.ad_archive_id else ""

    @property
    def dias_ativos(self) -> int:
        """Tempo de veiculação em dias. `total_active_time` do endpoint de busca vem
        None na prática (confirmado em live) — calcula de start_date/end_date."""
        try:
            v = int(self.total_active_time)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
        if not self.start_date:
            return 0
        import time
        end = self.end_date if (self.end_date and not self.is_active) else time.time()
        try:
            return max(0, int((float(end) - float(self.start_date)) / 86400))
        except (TypeError, ValueError):
            return 0

    @property
    def cover_url(self) -> str:
        imgs = self.snapshot.images or []
        if imgs:
            first = imgs[0]
            if isinstance(first, dict):
                return first.get("original_image_url") or first.get("resized_image_url") or ""
        vids = self.snapshot.videos or []
        if vids and isinstance(vids[0], dict):
            u = vids[0].get("video_preview_image_url") or ""
            if u:
                return u
        # anúncio carrossel: mídia fica em cards[], não em images/videos
        for card in self.snapshot.cards or []:
            if not isinstance(card, dict):
                continue
            u = card.get("original_image_url") or card.get("video_preview_image_url") or card.get("resized_image_url")
            if u:
                return u
        return ""


def facebook_ad_to_item(a: dict[str, Any]) -> AdItem:
    """Normaliza item de /facebook/adLibrary/search/ads (searchResults)."""
    return AdItem(
        ad_archive_id=str(a.get("ad_archive_id", "")),
        page_id=str(a.get("page_id", "")),
        page_name=a.get("page_name", "") or "",
        is_active=bool(a.get("is_active", False)),
        start_date=a.get("start_date"),
        end_date=a.get("end_date"),
        total_active_time=a.get("total_active_time", 0),
        collation_count=int(a.get("collation_count") or 0),
        publisher_platform=a.get("publisher_platform", []) or [],
        snapshot=AdSnapshot.model_validate(a.get("snapshot", {}) or {}),
    )
