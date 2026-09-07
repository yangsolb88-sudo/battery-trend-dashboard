import asyncio
import calendar
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "21600"))
NEWS_LOOKBACK_MONTHS = int(os.getenv("NEWS_LOOKBACK_MONTHS", "6"))
USER_AGENT = "UsedBatteryCircularBriefing/9.0 (+Render; fast public-data dashboard)"

app = FastAPI(title="Used Battery Recycling Monitor", version="9.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_dashboard_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_article_cache: dict[str, tuple[float, Any]] = {}
_refresh_lock = asyncio.Lock()

# GDELT DOC 2.0 offers a rolling historical search horizon. The UI exposes only
# months that are still inside the last 12 months so the archive never promises
# dates the free endpoint cannot search.
GDELT_HISTORY_DAYS = 365
MAX_ARTICLES_PER_ARCHIVE_REQUEST = int(os.getenv("MAX_ARTICLES_PER_ARCHIVE_REQUEST", "250"))
FAST_ARTICLE_LIMIT = int(os.getenv("FAST_ARTICLE_LIMIT", "40"))
DASHBOARD_PREVIEW_LIMIT = int(os.getenv("DASHBOARD_PREVIEW_LIMIT", "6"))

KEYWORDS = [
    {"ko": "사용후 배터리 재활용", "en": "End-of-life battery recycling", "query": '"end-of-life battery recycling"'},
    {"ko": "배터리 재생원료", "en": "Recycled battery materials", "query": '"recycled battery materials"'},
    {"ko": "배터리 순환경제", "en": "Battery circular economy", "query": '"battery circular economy"'},
    {"ko": "배터리 재생원료 인증", "en": "Recycled battery materials certification", "query": '"recycled content certification"'},
    {"ko": "배터리 여권", "en": "Battery passport", "query": '"battery passport"'},
]

CATEGORY_META = {
    "market": {
        "title": "시장 및 수요",
        "subtitle": "사용후 배터리 발생·회수·재활용 수요·재생원료 시장",
        "queries": [
            '"battery recycling market"',
            '"end-of-life battery" (market OR demand OR supply OR capacity)',
            '"black mass" (market OR price OR supply OR demand)',
            '"recycled battery materials" (demand OR market OR offtake)',
            '"second life battery" (market OR demand)',
        ],
    },
    "briefing": {
        "title": "주간 브리핑",
        "subtitle": "핵심 5개 키워드 기준 최근 이슈",
        "queries": [x["query"] for x in KEYWORDS] + [
            '"used battery recycling"',
            '"spent battery recycling"',
        ],
    },
    "policy": {
        "title": "정책 동향",
        "subtitle": "법령·EPR·재생원료·배터리여권·인증제도",
        "queries": [
            '"battery recycling regulation"',
            '"battery passport"',
            '"recycled content" battery',
            '"extended producer responsibility" battery',
            '"recycled content certification" battery',
            '"EU Battery Regulation" recycling',
        ],
    },
    "company": {
        "title": "기업 동향",
        "subtitle": "국내 사용후 배터리·재활용·재생원료 사업",
        "queries": [
            '"battery recycling" (SungEel OR Sebitchem OR EcoPro OR POSCO)',
            '"used battery" ("LG Energy Solution" OR "SK On" OR "Samsung SDI")',
            '"black mass" (Korea OR Korean)',
            '"recycled battery materials" (Korea OR Korean)',
            '"battery recycling plant" (Korea OR Korean)',
        ],
    },
    "technology": {
        "title": "기술 동향",
        "subtitle": "회수·전처리·습식/건식·직접재활용·진단·재사용",
        "queries": [
            '"battery recycling" hydrometallurgy',
            '"battery recycling" pyrometallurgy',
            '"direct recycling" battery',
            '"black mass" lithium recovery',
            '"end-of-life battery" disassembly',
            '"second life battery" diagnostics',
            '"LFP recycling"',
        ],
    },
}


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


def build_url(base: str, params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{base}?{urlencode(clean, doseq=True)}"


def request_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def source(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url}


# ---------------------------------------------------------------------------
# Official market / demand reference indicators
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
                payload = request_json(
                    "https://comtradeapi.un.org/data/v1/get/C/A/HS",
                    params={**params, "subscription-key": key},
                )
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
        raise RuntimeError("UN Comtrade 최근 한국 HS 850760 데이터를 찾지 못함")
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
        "source": source("U.S. EIA · Operating battery storage", "https://www.eia.gov/opendata/browser/electricity/operating-generator-capacity"),
    }


def world_bank_manufacturing() -> dict[str, Any]:
    countries = "KOR;CHN;USA;EUU"
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/NV.IND.MANF.ZS"
    payload = request_json(url, params={"format": "json", "date": f"{now_kst().year-6}:{now_kst().year}", "per_page": "200"})
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("World Bank 응답 형식 확인 필요")
    wanted = {"KOR": "한국", "CHN": "중국", "USA": "미국", "EUU": "EU"}
    latest: dict[str, dict[str, Any]] = {}
    for row in payload[1] or []:
        code = (row.get("countryiso3code") or "").upper()
        val = safe_float(row.get("value"))
        if code not in wanted or val is None:
            continue
        year = int(row.get("date"))
        if code not in latest or year > latest[code]["year"]:
            latest[code] = {"country": wanted[code], "year": year, "value": val}
    if not latest:
        raise RuntimeError("World Bank 제조업 지표 없음")
    return {
        "rows": [latest[c] for c in ("KOR", "CHN", "USA", "EUU") if c in latest],
        "source": source("World Bank · Manufacturing, value added (% of GDP)", "https://data.worldbank.org/indicator/NV.IND.MANF.ZS"),
    }


# ---------------------------------------------------------------------------
# GDELT article archive
# ---------------------------------------------------------------------------

def parse_gdelt_date(value: Any) -> str:
    raw = str(value or "")
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw[:10]


def _gdelt_fetch(query: str, start_dt: datetime, end_dt: datetime, maxrecords: int = 250) -> list[dict[str, Any]]:
    payload = request_json(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "artlist",
            "maxrecords": str(min(maxrecords, 250)),
            "startdatetime": start_dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "enddatetime": end_dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
            "format": "json",
        },
    )
    return payload.get("articles") or []


def _normalize_article(article: dict[str, Any]) -> dict[str, Any] | None:
    url = str(article.get("url") or "").strip()
    title = str(article.get("title") or "").strip()
    if not url or not title:
        return None
    return {
        "title": title,
        "url": url,
        "domain": str(article.get("domain") or ""),
        "date": parse_gdelt_date(article.get("seendate") or ""),
        "country": str(article.get("sourcecountry") or ""),
        "language": str(article.get("language") or ""),
    }


def _is_korean_article(article: dict[str, Any]) -> bool:
    country = (article.get("country") or "").replace(" ", "").lower()
    domain = (article.get("domain") or "").lower()
    return country in {"southkorea", "koreasouth", "republicofkorea", "korea"} or domain.endswith(".kr")


def _dedupe_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True):
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _collect_window(query: str, start_dt: datetime, end_dt: datetime, *, depth: int = 0, exhaustive: bool = False) -> list[dict[str, Any]]:
    """Fast ArticleList retrieval. Exhaustive split is optional and off by default for quick UI loads."""
    raw = _gdelt_fetch(query, start_dt, end_dt, 80 if not exhaustive else 250)
    duration = end_dt - start_dt
    if exhaustive and len(raw) >= 250 and duration > timedelta(days=7) and depth < 4:
        mid = start_dt + duration / 2
        left = _collect_window(query, start_dt, mid, depth=depth + 1, exhaustive=True)
        time.sleep(0.1)
        right = _collect_window(query, mid + timedelta(seconds=1), end_dt, depth=depth + 1, exhaustive=True)
        return _dedupe_articles(left + right)
    return [x for x in (_normalize_article(a) for a in raw) if x]


def _clip_to_history(start_dt: datetime, end_dt: datetime) -> tuple[datetime, datetime]:
    now = datetime.now(KST)
    earliest = now - timedelta(days=GDELT_HISTORY_DAYS)
    start_dt = max(start_dt, earliest)
    end_dt = min(end_dt, now)
    if end_dt < start_dt:
        raise ValueError("무료 GDELT 검색 가능기간(최근 12개월) 밖의 날짜임")
    return start_dt, end_dt


def date_window(*, period: str, year: int | None = None, month: int | None = None) -> tuple[datetime, datetime, str]:
    now = now_kst()
    if period == "week":
        return now - timedelta(days=7), now, "최근 7일"
    if period == "sixmonths":
        return now - timedelta(days=183), now, "최근 6개월"
    if period == "month":
        y = year or now.year
        m = month or now.month
        last_day = calendar.monthrange(y, m)[1]
        start = datetime(y, m, 1, 0, 0, 0, tzinfo=KST)
        end = datetime(y, m, last_day, 23, 59, 59, tzinfo=KST)
        start, end = _clip_to_history(start, end)
        return start, end, f"{y}년 {m}월"
    if period == "year":
        y = year or now.year
        start = datetime(y, 1, 1, 0, 0, 0, tzinfo=KST)
        end = datetime(y, 12, 31, 23, 59, 59, tzinfo=KST)
        start, end = _clip_to_history(start, end)
        return start, end, f"{y}년 전체"
    raise ValueError("지원하지 않는 기간")


def build_queries(category: str, scope: str) -> list[str]:
    if category not in CATEGORY_META:
        raise ValueError("지원하지 않는 카테고리")
    queries = list(CATEGORY_META[category]["queries"])
    if category == "company":
        scope = "domestic"
    if scope == "domestic":
        return [f"{q} sourcecountry:southkorea" for q in queries]
    return queries


def gdelt_archive(category: str, scope: str, start_dt: datetime, end_dt: datetime, *, limit: int | None = None, exhaustive: bool = False) -> list[dict[str, Any]]:
    queries = build_queries(category, scope)
    max_items = limit or FAST_ARTICLE_LIMIT
    items: list[dict[str, Any]] = []
    # Fast mode: stop as soon as enough relevant articles are collected.
    for q in queries:
        try:
            batch = _collect_window(q, start_dt, end_dt, exhaustive=exhaustive)
        except Exception:
            batch = []
        if scope == "global":
            batch = [a for a in batch if not _is_korean_article(a)]
        elif scope == "domestic":
            batch = [a for a in batch if _is_korean_article(a)]
        items.extend(batch)
        items = _dedupe_articles(items)
        if len(items) >= max_items:
            break
        time.sleep(0.05)
    return _dedupe_articles(items)[:max_items]


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


def available_archive() -> dict[str, Any]:
    now = now_kst()
    earliest = now - timedelta(days=GDELT_HISTORY_DAYS)
    months: list[dict[str, int]] = []
    cursor = datetime(earliest.year, earliest.month, 1, tzinfo=KST)
    final = datetime(now.year, now.month, 1, tzinfo=KST)
    while cursor <= final:
        months.append({"year": cursor.year, "month": cursor.month})
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    years = sorted({x["year"] for x in months}, reverse=True)
    return {"years": years, "months": months, "history_note": "무료 GDELT DOC API 기준 최근 12개월 범위"}


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

def generate_dashboard_sync() -> dict[str, Any]:
    now = now_kst()
    connections: dict[str, Any] = {}
    kpis: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []

    try:
        trade = comtrade_trade()
        connections["comtrade"] = {"status": "ok", "label": "UN Comtrade"}
        if trade.get("exports"):
            kpis.append({"label": "한국 Li-ion 배터리 수출", "value": fmt_money(trade["exports"]["value"]), "change": trade.get("export_change"), "meta": f"{trade['exports']['year']} · HS 850760"})
        if trade.get("imports"):
            kpis.append({"label": "한국 Li-ion 배터리 수입", "value": fmt_money(trade["imports"]["value"]), "change": trade.get("import_change"), "meta": f"{trade['imports']['year']} · HS 850760"})
        sources.append(trade["source"])
    except Exception as exc:
        connections["comtrade"] = {"status": "error", "label": "UN Comtrade", "detail": str(exc)[:180]}

    try:
        eia = eia_battery_capacity()
        connections["eia"] = {"status": "ok", "label": "U.S. EIA"}
        kpis.append({"label": "미국 운영 BESS 용량", "value": f"{eia['capacity_mw']/1000:,.2f} GW", "change": None, "meta": eia["period"]})
        sources.append(eia["source"])
    except Exception as exc:
        connections["eia"] = {"status": "error", "label": "U.S. EIA", "detail": str(exc)[:180]}

    try:
        wb = world_bank_manufacturing()
        connections["worldbank"] = {"status": "ok", "label": "World Bank"}
        kr = next((x for x in wb["rows"] if x["country"] == "한국"), None)
        if kr:
            kpis.append({"label": "한국 제조업 부가가치", "value": f"{kr['value']:.1f}%", "change": None, "meta": f"GDP 대비 · {kr['year']}"})
        sources.append(wb["source"])
    except Exception as exc:
        connections["worldbank"] = {"status": "limited", "label": "World Bank", "detail": str(exc)[:180]}

    # Lightweight previews only. No six-month search is executed on first page load.
    weekly: dict[str, Any] = {}
    start, end, _ = date_window(period="week")
    for scope in ("domestic", "global"):
        try:
            items = gdelt_archive("briefing", scope, start, end, limit=DASHBOARD_PREVIEW_LIMIT)
            weekly[scope] = {"count": len(items), "preview": items[:DASHBOARD_PREVIEW_LIMIT]}
            connections[f"gdelt_{scope}"] = {"status": "ok", "label": f"GDELT {scope}"}
        except Exception as exc:
            weekly[scope] = {"count": 0, "preview": []}
            connections[f"gdelt_{scope}"] = {"status": "error", "label": f"GDELT {scope}", "detail": str(exc)[:180]}

    if not kpis:
        kpis = [
            {"label": "시장지표", "value": "연결 대기", "change": None, "meta": "무료 API 확인 필요"},
            {"label": "주간 국내기사", "value": str(weekly.get("domestic", {}).get("count", 0)), "change": None, "meta": "최근 7일"},
            {"label": "주간 해외기사", "value": str(weekly.get("global", {}).get("count", 0)), "change": None, "meta": "최근 7일"},
        ]

    bad = [x for x in connections.values() if x["status"] == "error"]
    return {
        "status": "partial" if bad else "live",
        "generated_at": now.isoformat(),
        "kpis": kpis[:4],
        "weekly": weekly,
        "connections": connections,
        "sources": sources,
        "archive": available_archive(),
        "keywords": KEYWORDS,
        "default_lookback_months": NEWS_LOOKBACK_MONTHS,
        "categories": {k: {"title": v["title"], "subtitle": v["subtitle"]} for k, v in CATEGORY_META.items()},
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


@app.get("/api/archive/options")
def api_archive_options() -> dict[str, Any]:
    return available_archive()


@app.get("/api/articles")
async def api_articles(
    category: str = Query("briefing"),
    scope: str = Query("domestic"),
    period: str = Query("sixmonths"),
    year: int | None = Query(None),
    month: int | None = Query(None, ge=1, le=12),
    refresh: int = Query(0),
) -> dict[str, Any]:
    if category == "company":
        scope = "domestic"
    if scope not in {"domestic", "global", "all"}:
        raise ValueError("scope는 domestic/global/all 중 하나")
    if category not in CATEGORY_META:
        raise ValueError("지원하지 않는 category")

    start, end, label = date_window(period=period, year=year, month=month)
    key = f"{category}|{scope}|{period}|{year}|{month}|{start.isoformat()}|{end.isoformat()}"
    if not refresh:
        cached = cache_get(key)
        if cached is not None:
            return cached

    def run() -> list[dict[str, Any]]:
        # Default searches are intentionally capped for fast screen rendering.
        # Six-month/year searches are still supported but remain capped unless MAX_ARTICLES_PER_ARCHIVE_REQUEST is raised.
        request_limit = 60 if period == "week" else 120
        if scope == "all":
            return _dedupe_articles(
                gdelt_archive(category, "domestic", start, end, limit=request_limit // 2)
                + gdelt_archive(category, "global", start, end, limit=request_limit // 2)
            )[:request_limit]
        return gdelt_archive(category, scope, start, end, limit=request_limit)

    articles = await asyncio.to_thread(run)
    data = {
        "status": "ok",
        "category": category,
        "scope": scope,
        "period": period,
        "period_label": label,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "count": len(articles),
        "articles": articles,
        "note": "빠른 화면 표시를 위해 조회 결과를 제한하여 표시함. 최근 6개월/연도 전체는 참고용이며 무료 검색 API 특성상 전체 인터넷 기사 망라를 보장하지 않음.",
    }
    cache_set(key, data)
    return data


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
        "archive": data.get("archive"),
    }
