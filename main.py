import asyncio
import calendar
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlencode
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "21600"))
USER_AGENT = "RESETUsedBatteryCircularBriefing/10.0 (+public-open-data-monitor)"

app = FastAPI(title="RESET Used Battery Circular Briefing", version="10.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_dashboard_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_article_cache: dict[str, tuple[float, Any]] = {}
_refresh_lock = asyncio.Lock()

KEYWORDS = [
    {"ko": "사용후 배터리 재활용", "en": "End-of-life battery recycling", "jp": "使用済み電池リサイクル", "zh": "退役电池回收利用"},
    {"ko": "배터리 재생원료", "en": "Recycled battery materials", "jp": "電池再生原料", "zh": "电池再生原料"},
    {"ko": "배터리 순환경제", "en": "Battery circular economy", "jp": "電池循環経済", "zh": "电池循环经济"},
    {"ko": "배터리 재생원료 인증", "en": "Recycled battery materials certification", "jp": "電池再生原料認証", "zh": "电池再生原料认证"},
    {"ko": "배터리 여권", "en": "Battery passport", "jp": "バッテリーパスポート", "zh": "电池护照"},
]

CATEGORY_META = {
    "briefing": {"title": "종합 브리핑", "subtitle": "사용후 배터리 재활용·재생원료 핵심 이슈", "ko": ["사용후 배터리", "폐배터리", "배터리 재활용", "배터리 재생원료", "배터리 여권", "배터리 순환경제"], "en": ["end-of-life battery recycling", "used battery recycling", "battery recycling", "recycled battery materials", "battery passport", "battery circular economy"]},
    "policy": {"title": "정책·제도", "subtitle": "재생원료 인증·배터리 여권·EPR·규제", "ko": ["배터리 재생원료 인증", "배터리 여권", "폐배터리 규제", "사용후 배터리 제도", "배터리 순환경제"], "en": ["battery passport", "recycled battery materials certification", "EU Battery Regulation recycling", "battery EPR", "battery recycling regulation"]},
    "company": {"title": "기업·투자", "subtitle": "국내외 재활용 기업·공장·투자", "ko": ["성일하이텍", "새빗켐", "포스코HY클린메탈", "에코프로씨엔지", "폐배터리 재활용 기업"], "en": ["SungEel battery recycling", "Li-Cycle", "Redwood Materials battery recycling", "Ascend Elements", "Cirba Solutions", "black mass recycling plant"]},
    "materials": {"title": "재생원료·기술", "subtitle": "블랙매스·Li/Ni/Co 회수·전처리·습식제련", "ko": ["블랙매스", "리튬 회수", "니켈 회수", "코발트 회수", "폐배터리 습식제련", "직접재활용"], "en": ["black mass", "lithium recovery battery recycling", "nickel cobalt recovery battery", "hydrometallurgy battery recycling", "direct recycling battery", "LFP recycling"]},
    "market": {"title": "시장·수요", "subtitle": "배터리 수출입·BESS·재생원료 수요", "ko": ["사용후 배터리 시장", "폐배터리 시장", "배터리 재활용 시장", "재생원료 수요"], "en": ["battery recycling market", "end-of-life battery market", "black mass market", "recycled battery materials demand", "second life battery market"]},
}

NEGATIVE_PATTERNS = [
    r"airpod", r"iphone", r"ipad", r"smartphone", r"laptop", r"power bank", r"phone battery", r"replace.*battery",
    r"dead battery", r"car won't start", r"jump start", r"review", r"suv", r"family vehicle", r"watch battery",
    r"에어팟", r"아이폰", r"휴대폰", r"스마트폰", r"노트북", r"보조배터리", r"시동", r"방전", r"교체", r"리뷰",
]
POSITIVE_PATTERNS = [
    r"recycl", r"reuse", r"second[- ]life", r"end[- ]of[- ]life", r"used battery", r"spent battery", r"black mass",
    r"battery passport", r"circular", r"recycled.*material", r"recovery", r"hydrometallurgy", r"pyrometallurgy",
    r"폐배터리", r"사용후", r"재활용", r"재생원료", r"블랙매스", r"회수", r"순환경제", r"배터리 여권", r"인증",
    r"使用済み", r"リサイクル", r"退役", r"回收", r"循环", r"护照",
]


def now_kst() -> datetime:
    return datetime.now(KST)


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "–"
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def request_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def request_text(url: str, *, params: dict[str, Any] | None = None) -> str:
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml,*/*"}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.text


def source(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url}


# ---------------------------------------------------------------------------
# Free official/open indicators
# ---------------------------------------------------------------------------

def comtrade_trade() -> dict[str, Any]:
    key = os.getenv("COMTRADE_API_KEY", "").strip()
    current_year = now_kst().year
    years = [current_year - i for i in range(1, 5)]

    def fetch_one(year: int, flow: str) -> list[dict[str, Any]]:
        params = {
            "reporterCode": "410", "partnerCode": "0", "cmdCode": "850760",
            "flowCode": flow, "period": str(year), "maxrecords": "50", "includeDesc": "true",
        }
        if key:
            try:
                payload = request_json("https://comtradeapi.un.org/data/v1/get/C/A/HS", params={**params, "subscription-key": key})
                return payload.get("data") or []
            except Exception:
                pass
        payload = request_json("https://comtradeapi.un.org/public/v1/preview/C/A/HS", params=params)
        return payload.get("data") or []

    def normalize(rows: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
        for row in rows:
            value = safe_float(row.get("primaryValue"))
            if value is None:
                continue
            ref_year = row.get("refYear") or row.get("period") or year
            try:
                ref_year = int(str(ref_year)[:4])
            except Exception:
                ref_year = year
            return {"year": ref_year, "value": value}
        return None

    result: dict[str, list[dict[str, Any]]] = {"X": [], "M": []}
    for flow in ("X", "M"):
        for year in years:
            try:
                item = normalize(fetch_one(year, flow), year)
            except Exception:
                item = None
            if item:
                result[flow].append(item)
            if len(result[flow]) >= 2:
                break

    exports = sorted(result["X"], key=lambda x: x["year"], reverse=True)
    imports = sorted(result["M"], key=lambda x: x["year"], reverse=True)
    if not exports and not imports:
        raise RuntimeError("UN Comtrade 한국 HS 850760 데이터를 찾지 못함")
    return {
        "exports": exports[0] if exports else None,
        "imports": imports[0] if imports else None,
        "export_change": pct_change(exports[0]["value"], exports[1]["value"]) if len(exports) > 1 else None,
        "import_change": pct_change(imports[0]["value"], imports[1]["value"]) if len(imports) > 1 else None,
        "source": source("UN Comtrade · Korea HS 850760", "https://comtradeplus.un.org/"),
    }


def eia_battery_capacity() -> dict[str, Any]:
    key = os.getenv("EIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("EIA_API_KEY 미설정")
    base = "https://api.eia.gov/v2/electricity/operating-generator-capacity/data/"
    common = {
        "api_key": key,
        "frequency": "monthly",
        "data[0]": "nameplate-capacity-mw",
        "facets[energy_source_code][]": "MWH",
        "facets[status][]": "OP",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
    }
    latest = request_json(base, params={**common, "length": "1", "offset": "0"})
    rows = latest.get("response", {}).get("data", [])
    if not rows:
        raise RuntimeError("EIA 최신 기간 없음")
    period = str(rows[0].get("period") or "")
    payload = request_json(base, params={**common, "start": period, "end": period, "length": "5000", "offset": "0"})
    total_mw = sum(safe_float(r.get("nameplate-capacity-mw")) or 0 for r in payload.get("response", {}).get("data", []))
    if total_mw <= 0:
        raise RuntimeError("EIA BESS 용량 값 없음")
    return {
        "period": period,
        "capacity_mw": total_mw,
        "source": source("U.S. EIA · Utility-scale battery storage", "https://www.eia.gov/opendata/browser/electricity/operating-generator-capacity"),
    }


def world_bank_manufacturing() -> dict[str, Any]:
    url = "https://api.worldbank.org/v2/country/KOR/indicator/NV.IND.MANF.ZS"
    payload = request_json(url, params={"format": "json", "date": f"{now_kst().year-6}:{now_kst().year}", "per_page": "50"})
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("World Bank 응답 형식 확인 필요")
    latest = None
    for row in payload[1] or []:
        val = safe_float(row.get("value"))
        if val is not None:
            latest = {"year": int(row.get("date")), "value": val}
            break
    if not latest:
        raise RuntimeError("World Bank 제조업 지표 없음")
    return {"latest": latest, "source": source("World Bank · Manufacturing, value added (% of GDP)", "https://data.worldbank.org/indicator/NV.IND.MANF.ZS")}


# ---------------------------------------------------------------------------
# News: Google News RSS first, GDELT fallback. No API key required.
# ---------------------------------------------------------------------------

def _parse_rss_date(text: str) -> str:
    try:
        return parsedate_to_datetime(text).astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        return text[:10] if text else ""


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ").strip()


def _passes_filter(title: str, desc: str = "") -> bool:
    hay = f"{title} {desc}".lower()
    if any(re.search(p, hay, re.I) for p in NEGATIVE_PATTERNS):
        # Allow if a strongly relevant recycling term also appears.
        strong = [r"recycl", r"black mass", r"battery passport", r"폐배터리", r"재생원료", r"순환경제"]
        if not any(re.search(p, hay, re.I) for p in strong):
            return False
    return any(re.search(p, hay, re.I) for p in POSITIVE_PATTERNS)


def _dedupe_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _google_rss(scope: str, category: str, period: str, limit: int) -> list[dict[str, Any]]:
    meta = CATEGORY_META.get(category, CATEGORY_META["briefing"])
    when = "7d" if period == "week" else "6m"
    if scope == "domestic":
        terms = meta["ko"][:5]
        q = " OR ".join([f'"{t}"' if " " in t else t for t in terms]) + f" when:{when}"
        params = {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        terms = meta["en"][:5]
        q = " OR ".join([f'"{t}"' if " " in t else t for t in terms]) + f" when:{when} -AirPods -iPhone -smartphone -laptop"
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    xml_text = request_text("https://news.google.com/rss/search", params=params)
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = _parse_rss_date(item.findtext("pubDate") or "")
        desc = _clean_html(item.findtext("description") or "")
        source_el = item.find("source")
        domain = (source_el.text or "Google News") if source_el is not None else "Google News"
        if not title or not link:
            continue
        if not _passes_filter(title, desc):
            continue
        items.append({"title": title, "url": link, "domain": domain, "date": pub, "language": "ko" if scope == "domestic" else "en", "source": "Google News RSS"})
        if len(items) >= limit:
            break
    return _dedupe_articles(items)[:limit]


def _gdelt_fetch(scope: str, category: str, period: str, limit: int) -> list[dict[str, Any]]:
    meta = CATEGORY_META.get(category, CATEGORY_META["briefing"])
    now = now_kst()
    start = now - (timedelta(days=7) if period == "week" else timedelta(days=183))
    if scope == "domestic":
        query = " OR ".join([f'"{x}"' for x in meta["ko"][:4]]) + " sourcecountry:southkorea"
    else:
        query = " OR ".join([f'"{x}"' for x in meta["en"][:4]])
    payload = request_json(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "artlist",
            "maxrecords": str(min(limit * 3, 75)),
            "startdatetime": start.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "enddatetime": now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
            "format": "json",
        },
    )
    out = []
    for a in payload.get("articles") or []:
        title = str(a.get("title") or "").strip()
        url = str(a.get("url") or "").strip()
        if not title or not url:
            continue
        if not _passes_filter(title):
            continue
        out.append({"title": title, "url": url, "domain": str(a.get("domain") or ""), "date": str(a.get("seendate") or "")[:8], "language": str(a.get("language") or ""), "source": "GDELT"})
        if len(out) >= limit:
            break
    return _dedupe_articles(out)[:limit]


def news_articles(category: str, scope: str, period: str, limit: int = 8) -> dict[str, Any]:
    key = f"articles|{category}|{scope}|{period}|{limit}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    items: list[dict[str, Any]] = []
    method = "Google News RSS"
    errors: list[str] = []
    try:
        items = _google_rss(scope, category, period, limit)
    except Exception as exc:
        errors.append(f"Google News RSS: {str(exc)[:100]}")
    if len(items) < 3:
        try:
            gdelt_items = _gdelt_fetch(scope, category, period, limit)
            items = _dedupe_articles(items + gdelt_items)[:limit]
            if gdelt_items:
                method = "Google News RSS + GDELT"
        except Exception as exc:
            errors.append(f"GDELT: {str(exc)[:100]}")
    data = {
        "status": "ok" if items else "empty",
        "category": category,
        "scope": scope,
        "period": period,
        "count": len(items),
        "articles": items,
        "method": method,
        "errors": errors,
        "search_url": google_search_url(scope, category, period),
    }
    cache_set(key, data)
    return data


def google_search_url(scope: str, category: str, period: str) -> str:
    meta = CATEGORY_META.get(category, CATEGORY_META["briefing"])
    when = "7d" if period == "week" else "6m"
    if scope == "domestic":
        q = " OR ".join([f'"{t}"' for t in meta["ko"][:4]]) + f" when:{when}"
        params = {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        q = " OR ".join([f'"{t}"' for t in meta["en"][:4]]) + f" when:{when}"
        params = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return "https://news.google.com/search?" + urlencode(params)


def cache_get(key: str) -> Any | None:
    item = _article_cache.get(key)
    if not item:
        return None
    expires, data = item
    if expires <= time.time():
        _article_cache.pop(key, None)
        return None
    return data


def cache_set(key: str, data: Any) -> None:
    _article_cache[key] = (time.time() + CACHE_TTL, data)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def generate_dashboard_sync() -> dict[str, Any]:
    now = now_kst()
    connections: dict[str, Any] = {}
    kpis: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []

    try:
        trade = comtrade_trade()
        connections["comtrade"] = {"status": "ok", "label": "UN Comtrade", "detail": "한국 HS 850760 수출입"}
        if trade.get("exports"):
            kpis.append({"label": "한국 Li-ion 배터리 수출", "value": fmt_money(trade["exports"]["value"]), "change": trade.get("export_change"), "meta": f"{trade['exports']['year']} · HS 850760"})
        if trade.get("imports"):
            kpis.append({"label": "한국 Li-ion 배터리 수입", "value": fmt_money(trade["imports"]["value"]), "change": trade.get("import_change"), "meta": f"{trade['imports']['year']} · HS 850760"})
        sources.append(trade["source"])
    except Exception as exc:
        connections["comtrade"] = {"status": "limited", "label": "UN Comtrade", "detail": str(exc)[:140]}

    try:
        eia = eia_battery_capacity()
        connections["eia"] = {"status": "ok", "label": "U.S. EIA", "detail": "미국 운영 BESS 용량"}
        kpis.append({"label": "미국 BESS 운영용량", "value": f"{eia['capacity_mw']/1000:,.2f} GW", "change": None, "meta": eia["period"]})
        sources.append(eia["source"])
    except Exception as exc:
        connections["eia"] = {"status": "limited", "label": "U.S. EIA", "detail": str(exc)[:140]}

    try:
        wb = world_bank_manufacturing()
        connections["worldbank"] = {"status": "ok", "label": "World Bank", "detail": "한국 제조업 기반지표"}
        kr = wb["latest"]
        kpis.append({"label": "한국 제조업 부가가치", "value": f"{kr['value']:.1f}%", "change": None, "meta": f"GDP 대비 · {kr['year']}"})
        sources.append(wb["source"])
    except Exception as exc:
        connections["worldbank"] = {"status": "limited", "label": "World Bank", "detail": str(exc)[:140]}

    # News previews use RSS first so the page behaves like a briefing site, not a long-running crawler.
    weekly: dict[str, Any] = {}
    for scope in ("domestic", "global"):
        result = news_articles("briefing", scope, "week", limit=5)
        weekly[scope] = result
        connections[f"news_{scope}"] = {"status": "ok" if result["articles"] else "empty", "label": f"{scope} news", "detail": result.get("method", "")}

    while len(kpis) < 4:
        kpis.append({"label": "뉴스 모니터링", "value": str(sum(x.get("count", 0) for x in weekly.values())), "change": None, "meta": "최근 7일 기사"})

    return {
        "status": "live",
        "generated_at": now.isoformat(),
        "kpis": kpis[:4],
        "keywords": KEYWORDS,
        "categories": {k: {"title": v["title"], "subtitle": v["subtitle"]} for k, v in CATEGORY_META.items()},
        "weekly": weekly,
        "connections": connections,
        "sources": sources + [source("Google News RSS", "https://news.google.com/"), source("GDELT DOC 2.0", "https://www.gdeltproject.org/")],
        "note": "첫 화면은 최근 7일 RSS 기반 빠른 조회, 버튼 클릭 시 최근 6개월 기사 조회",
    }


async def get_dashboard(force: bool = False) -> dict[str, Any]:
    now_ts = time.time()
    if not force and _dashboard_cache["data"] is not None and _dashboard_cache["expires_at"] > now_ts:
        return _dashboard_cache["data"]
    async with _refresh_lock:
        now_ts = time.time()
        if not force and _dashboard_cache["data"] is not None and _dashboard_cache["expires_at"] > now_ts:
            return _dashboard_cache["data"]
        data = await asyncio.to_thread(generate_dashboard_sync)
        _dashboard_cache["data"] = data
        _dashboard_cache["expires_at"] = time.time() + CACHE_TTL
        return data


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboard")
async def api_dashboard(refresh: int = 0) -> dict[str, Any]:
    return await get_dashboard(force=bool(refresh))


@app.get("/api/articles")
def api_articles(
    category: str = Query("briefing"),
    scope: str = Query("domestic"),
    period: str = Query("week"),
    refresh: int = Query(0),
) -> dict[str, Any]:
    if category not in CATEGORY_META:
        category = "briefing"
    if scope not in {"domestic", "global", "all"}:
        scope = "domestic"
    if period not in {"week", "sixmonths"}:
        period = "week"

    if refresh:
        for key in list(_article_cache.keys()):
            if key.startswith(f"articles|{category}|"):
                _article_cache.pop(key, None)

    if scope == "all":
        domestic = news_articles(category, "domestic", period, limit=8)
        global_ = news_articles(category, "global", period, limit=8)
        articles = _dedupe_articles(domestic["articles"] + global_["articles"])[:16]
        return {"status": "ok" if articles else "empty", "category": category, "scope": scope, "period": period, "count": len(articles), "articles": articles, "method": "Google News RSS + GDELT", "search_url": domestic["search_url"]}

    return news_articles(category, scope, period, limit=12 if period == "sixmonths" else 8)


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    data = await get_dashboard()
    return {
        "status": data.get("status"),
        "generated_at": data.get("generated_at"),
        "connections": data.get("connections"),
        "environment": {
            "COMTRADE_API_KEY": bool(os.getenv("COMTRADE_API_KEY", "").strip()),
            "EIA_API_KEY": bool(os.getenv("EIA_API_KEY", "").strip()),
        },
    }
