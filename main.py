import asyncio
import calendar
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "21600"))
NEWS_LOOKBACK_MONTHS = int(os.getenv("NEWS_LOOKBACK_MONTHS", "6"))
HTTP_TIMEOUT = 24
USER_AGENT = "UsedBatteryRecyclingBriefing/6.0 (+https://render.com; free-public-data)"

app = FastAPI(title="Used Battery Recycling Briefing", version="6.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_refresh_lock = asyncio.Lock()

SECTION_KEYS = [
    "DOMESTIC_NEWS",
    "GLOBAL_NEWS",
    "POLICY",
    "COMPANIES",
    "RECYCLING_TECH",
    "MARKET_STATS",
]

SECTION_META = {
    "DOMESTIC_NEWS": {"title": "국내 사용후 배터리 뉴스", "subtitle": "사용후 배터리 · 폐배터리 · 재활용 · 재사용"},
    "GLOBAL_NEWS": {"title": "해외 사용후 배터리 뉴스", "subtitle": "End-of-life battery · Battery recycling · Black mass"},
    "POLICY": {"title": "정책·규제", "subtitle": "재생원료 인증 · 배터리 여권 · EU Battery Regulation · EPR"},
    "COMPANIES": {"title": "기업·투자 동향", "subtitle": "국내외 재활용 기업 · 공장 · MOU · 투자"},
    "RECYCLING_TECH": {"title": "재활용·재생원료 기술", "subtitle": "블랙매스 · Li/Ni/Co 회수 · 습식·건식·직접재활용"},
    "MARKET_STATS": {"title": "시장·기반 통계", "subtitle": "한국 HS 850760 무역 · 미국 BESS · 제조업 기반지표"},
}

KEYWORD_CATALOG = [
    {"no": 1, "ko": "사용후 배터리 재활용", "en": "End-of-life battery recycling", "ja": "使用済み電池リサイクル", "zh": "退役电池回收利用"},
    {"no": 2, "ko": "배터리 재생원료", "en": "Recycled battery materials", "ja": "電池再生原料", "zh": "电池再生原料"},
    {"no": 3, "ko": "배터리 순환경제", "en": "Battery circular economy", "ja": "電池循環経済", "zh": "电池循环经济"},
    {"no": 4, "ko": "배터리 재생원료 인증", "en": "Recycled battery materials certification", "ja": "電池再生原料認証", "zh": "电池再生原料认证"},
    {"no": 5, "ko": "배터리 여권", "en": "Battery passport", "ja": "バッテリーパスポート", "zh": "电池护照"},
]

# Broad terms used to keep GDELT results on used batteries / recycling.
INCLUDE_TERMS = [
    "사용후 배터리", "폐배터리", "배터리 재활용", "재사용 배터리", "재생원료", "배터리 여권", "순환경제",
    "end-of-life battery", "end of life battery", "used battery", "spent battery", "battery recycling",
    "battery recycler", "battery recycl", "black mass", "recycled battery material", "recycled content",
    "battery passport", "battery circular", "second life battery", "lithium-ion battery recycling", "lithium battery recycling",
    "退役电池", "电池回收", "电池再生", "使用済み電池", "電池リサイクル", "電池再生",
]

STRONG_TERMS = [
    "사용후", "폐배터리", "재활용", "재생원료", "black mass", "recycling", "recycled", "recycler", "end-of-life", "spent battery", "battery passport",
]

EXCLUDE_TERMS = [
    "airpod", "iphone battery", "phone battery", "laptop battery replacement", "battery drain", "battery life", "replace a dying", "toyota fastest-growing family",
    "aa battery", "aaa battery", "watch battery", "power bank review", "charging tips", "battery test", "recall", "fire", "explosion"
]

DOMESTIC_QUERY = '(("사용후 배터리" OR "폐배터리" OR "배터리 재활용" OR "배터리 재생원료" OR "배터리 여권" OR "배터리 순환경제") sourcecountry:southkorea)'
GLOBAL_QUERY = '(("end-of-life battery" OR "spent battery" OR "used lithium-ion battery" OR "battery recycling" OR "black mass" OR "recycled battery materials" OR "battery passport" OR "battery circular economy" OR "second life battery") -sourcecountry:southkorea)'
POLICY_QUERY = '(("battery passport" OR "EU Battery Regulation" OR "recycled content" OR "recycled battery materials certification" OR "end-of-life battery regulation" OR "extended producer responsibility" OR "critical raw materials act" OR "배터리 재생원료 인증" OR "배터리 여권" OR "사용후 배터리") )'
COMPANY_QUERY = '(("battery recycling" OR "black mass") (SungEel OR "Seongil HiTech" OR "SK ecoplant" OR "POSCO HY Clean Metal" OR "EcoPro CnG" OR Sebitchem OR "Li-Cycle" OR "Redwood Materials" OR "Ascend Elements" OR Cirba OR Umicore OR Ecobat OR BASF OR Hydrovolt))'
TECH_QUERY = '(("black mass" OR "lithium recovery" OR "nickel recovery" OR "cobalt recovery" OR hydrometallurgy OR pyrometallurgy OR "direct recycling" OR "battery recycling technology" OR "recycled battery materials"))'


def now_kst() -> datetime:
    return datetime.now(KST)


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "–"
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def fmt_number(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "–"
    return f"{float(value):,.{digits}f}"


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def build_url(base: str, params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{base}?{urlencode(clean, doseq=True)}"


def request_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    response = requests.get(url, params=params, headers=hdrs, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def source(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url}


def safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:220] if text else type(exc).__name__


def conn(label: str, status: str, detail: str = "", configured: bool | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"status": status, "label": label, "detail": detail}
    if configured is not None:
        item["configured"] = configured
    return item


# ---------------------------------------------------------------------------
# Official / public data APIs
# ---------------------------------------------------------------------------

def comtrade_trade() -> dict[str, Any]:
    """Korea lithium-ion accumulators HS 850760 annual trade with World."""
    key = os.getenv("COMTRADE_API_KEY", "").strip()
    current_year = now_kst().year
    years = [current_year - i for i in range(1, 7)]

    def fetch_one(year: int, flow: str) -> list[dict[str, Any]]:
        params = {
            "reporterCode": "410",
            "partnerCode": "0",
            "cmdCode": "850760",
            "flowCode": flow,
            "period": str(year),
            "maxrecords": "50",
            "includeDesc": "true",
        }
        errors = []
        if key:
            try:
                return (request_json("https://comtradeapi.un.org/data/v1/get/C/A/HS", params={**params, "subscription-key": key}).get("data") or [])
            except Exception as exc:
                errors.append(f"auth:{type(exc).__name__}")
        try:
            return (request_json("https://comtradeapi.un.org/public/v1/preview/C/A/HS", params=params).get("data") or [])
        except Exception as exc:
            errors.append(f"preview:{type(exc).__name__}")
            raise RuntimeError("UN Comtrade 호출 실패 (" + ", ".join(errors) + ")")

    def normalize(rows: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
        for row in rows:
            value = safe_float(row.get("primaryValue"))
            if value is not None:
                ref_year = row.get("refYear") or row.get("period") or year
                try:
                    ref_year = int(str(ref_year)[:4])
                except Exception:
                    ref_year = year
                return {"year": ref_year, "value": value}
        return None

    flows: dict[str, list[dict[str, Any]]] = {"X": [], "M": []}
    for flow in ("X", "M"):
        for year in years:
            try:
                item = normalize(fetch_one(year, flow), year)
            except Exception:
                item = None
            if item:
                flows[flow].append(item)
            if len(flows[flow]) >= 2:
                break
            time.sleep(0.1)

    exports = sorted(flows["X"], key=lambda x: x["year"], reverse=True)
    imports = sorted(flows["M"], key=lambda x: x["year"], reverse=True)
    if not exports and not imports:
        raise RuntimeError("한국 HS 850760 최근 연도 데이터를 찾지 못함")

    latest_year = max([x["year"] for x in exports[:1] + imports[:1]] or [current_year - 1])
    src_url = build_url(
        "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
        {"reporterCode": "410", "partnerCode": "0", "cmdCode": "850760", "flowCode": "X", "period": str(latest_year)},
    )
    return {
        "exports": exports[0] if exports else None,
        "imports": imports[0] if imports else None,
        "export_prev": exports[1] if len(exports) > 1 else None,
        "import_prev": imports[1] if len(imports) > 1 else None,
        "export_change": pct_change(exports[0]["value"] if exports else None, exports[1]["value"] if len(exports) > 1 else None),
        "import_change": pct_change(imports[0]["value"] if imports else None, imports[1]["value"] if len(imports) > 1 else None),
        "source": source("UN Comtrade · Korea HS 850760", src_url),
        "using_key": bool(key),
    }


def eia_battery_capacity() -> dict[str, Any]:
    """Latest U.S. utility-scale operating battery storage nameplate capacity."""
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
    latest_payload = request_json(base, params={**common, "length": "1", "offset": "0"})
    rows = latest_payload.get("response", {}).get("data", [])
    if not rows:
        raise RuntimeError("EIA 최신 기간 없음")
    period = str(rows[0].get("period") or "")
    payload = request_json(base, params={**common, "start": period, "end": period, "length": "5000"})
    data_rows = payload.get("response", {}).get("data", [])
    total_mw = 0.0
    count = 0
    states: dict[str, float] = {}
    for row in data_rows:
        cap = safe_float(row.get("nameplate-capacity-mw"))
        if cap is None:
            continue
        total_mw += cap
        count += 1
        state_id = row.get("stateid") or row.get("state")
        if state_id:
            states[str(state_id)] = states.get(str(state_id), 0.0) + cap
    return {
        "period": period,
        "capacity_mw": total_mw,
        "generator_count": count,
        "top_states": sorted(states.items(), key=lambda x: x[1], reverse=True)[:3],
        "source": source("U.S. EIA · Inventory of Operable Generators", "https://www.eia.gov/opendata/browser/electricity/operating-generator-capacity"),
    }


def world_bank_manufacturing() -> dict[str, Any]:
    countries = "KOR;CHN;USA;EUU"
    indicator = "NV.IND.MANF.ZS"
    end_year = now_kst().year
    start_year = end_year - 7
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
    payload = request_json(url, params={"format": "json", "date": f"{start_year}:{end_year}", "per_page": "200"})
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise RuntimeError("World Bank 응답 형식 확인 필요")
    wanted = {"KOR": "한국", "CHN": "중국", "USA": "미국", "EUU": "EU"}
    latest: dict[str, dict[str, Any]] = {}
    for row in payload[1]:
        if not isinstance(row, dict):
            continue
        code = (row.get("countryiso3code") or "").upper()
        value = safe_float(row.get("value"))
        if code not in wanted or value is None:
            continue
        try:
            year = int(row.get("date"))
        except Exception:
            continue
        if code not in latest or year > latest[code]["year"]:
            latest[code] = {"country": wanted[code], "year": year, "value": value}
    if not latest:
        raise RuntimeError("World Bank 제조업 지표 값 없음")
    return {
        "rows": [latest[c] for c in ("KOR", "CHN", "USA", "EUU") if c in latest],
        "source": source("World Bank · Manufacturing, value added (% of GDP)", "https://data.worldbank.org/indicator/NV.IND.MANF.ZS"),
    }


def eurostat_reference() -> dict[str, Any]:
    """Eurostat is kept as a non-blocking source link, not a KPI that can break the dashboard."""
    return {
        "source": source("Eurostat · New passenger cars by type of motor energy", "https://ec.europa.eu/eurostat/databrowser/view/road_eqr_carpda/default/table?lang=en"),
        "note": "EU 전기차 등록 기반지표 참고 링크",
    }


def alpha_quotes_optional() -> dict[str, Any] | None:
    if os.getenv("ENABLE_ALPHA", "false").strip().lower() not in {"1", "true", "yes", "y"}:
        return None
    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY 미설정")
    symbols = [s.strip().upper() for s in os.getenv("STOCK_SYMBOLS", "ALB,SQM,TSLA").split(",") if s.strip()][:2]
    out = []
    errors = []
    for idx, symbol in enumerate(symbols):
        if idx:
            time.sleep(12)  # conservative for free tiers
        payload = request_json("https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key})
        quote = payload.get("Global Quote") or {}
        if not quote:
            errors.append(str(payload.get("Information") or payload.get("Note") or payload.get("Error Message") or "empty")[:100])
            continue
        price = safe_float(quote.get("05. price"))
        if price is not None:
            out.append({
                "symbol": symbol,
                "price": price,
                "change_percent": safe_float(quote.get("10. change percent")),
                "latest_day": quote.get("07. latest trading day") or "",
                "source": source(f"Alpha Vantage · {symbol} Global Quote", "https://www.alphavantage.co/documentation/#latestprice"),
            })
    if not out:
        raise RuntimeError("Alpha Vantage: " + (" / ".join(errors) or "데이터 없음"))
    return {"quotes": out, "partial": len(out) < len(symbols)}


# ---------------------------------------------------------------------------
# GDELT news search
# ---------------------------------------------------------------------------

def parse_gdelt_date(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value[:10]


def shift_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def gdelt_fetch_window(query: str, start_dt: datetime, end_dt: datetime, maxrecords: int) -> list[dict[str, Any]]:
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


def article_score(article: dict[str, Any], *, domestic: bool | None = None, section: str = "") -> int:
    title = str(article.get("title") or "")
    domain = str(article.get("domain") or "")
    country = str(article.get("sourcecountry") or "")
    language = str(article.get("language") or "")
    text = f"{title} {domain} {country} {language}".lower()
    if any(term in text for term in EXCLUDE_TERMS):
        return -100
    score = 0
    for term in INCLUDE_TERMS:
        if term.lower() in text:
            score += 3
    for term in STRONG_TERMS:
        if term.lower() in text:
            score += 2
    if "battery" in text and ("recycl" in text or "black mass" in text or "passport" in text or "circular" in text or "second life" in text):
        score += 4
    if "lithium" in text and "recycl" in text:
        score += 3
    if section == "companies" and any(x.lower() in text for x in ["sungeel", "li-cycle", "redwood", "ascend", "cirba", "umicore", "hydrovolt", "ecobat", "sk ecoplant", "posco"]):
        score += 5
    if domestic is True and "south korea" in country.lower():
        score += 2
    if domestic is False and "south korea" not in country.lower():
        score += 1
    return score


def clean_articles(articles: list[dict[str, Any]], *, domestic: bool | None = None, section: str = "", limit: int = 12) -> list[dict[str, Any]]:
    cleaned = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        title = re.sub(r"\s+", " ", str(article.get("title") or "").strip())
        if not url or not title:
            continue
        key_title = re.sub(r"[^a-z0-9가-힣一-龥ぁ-んァ-ン]", "", title.lower())[:100]
        if url in seen_urls or key_title in seen_titles:
            continue
        score = article_score(article, domestic=domestic, section=section)
        if score < 3:
            continue
        seen_urls.add(url)
        seen_titles.add(key_title)
        cleaned.append({
            "title": title,
            "url": url,
            "domain": article.get("domain") or "",
            "date": parse_gdelt_date(article.get("seendate") or ""),
            "country": article.get("sourcecountry") or "",
            "language": article.get("language") or "",
            "score": score,
        })
    cleaned.sort(key=lambda x: (x.get("date") or "", x.get("score") or 0), reverse=True)
    return cleaned[:limit]


def gdelt_articles(query: str, *, maxrecords: int = 12, lookback_months: int = NEWS_LOOKBACK_MONTHS, domestic: bool | None = None, section: str = "") -> list[dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    start_utc = shift_months(now_utc, -max(1, lookback_months))
    windows: list[tuple[datetime, datetime]] = []
    cursor_end = now_utc
    while cursor_end > start_utc:
        cursor_start = max(shift_months(cursor_end, -3), start_utc)
        windows.append((cursor_start, cursor_end))
        cursor_end = cursor_start - timedelta(seconds=1)
    collected: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for window_start, window_end in windows:
        try:
            collected.extend(gdelt_fetch_window(query, window_start, window_end, max(maxrecords * 4, 40)))
        except Exception as exc:
            last_error = exc
        time.sleep(0.12)
    result = clean_articles(collected, domestic=domestic, section=section, limit=maxrecords)
    if not result and last_error:
        raise RuntimeError(f"GDELT 호출 실패: {type(last_error).__name__}")
    return result


def news_section(articles: list[dict[str, Any]], fallback: str, *, limit: int = 8) -> tuple[str, list[dict[str, str]]]:
    if not articles:
        return fallback, []
    lines: list[str] = []
    sources: list[dict[str, str]] = []
    for i, article in enumerate(articles[:limit], start=1):
        meta = " · ".join(x for x in [article.get("domain"), article.get("country"), article.get("date")] if x)
        lines.append(f"• {article['title']} [{i}]" + (f"\n  {meta}" if meta else ""))
        sources.append(source(article["title"], article["url"]))
    return "\n\n".join(lines), sources


def assign_global_source_numbers(sections: dict[str, Any]) -> list[dict[str, Any]]:
    all_sources: list[dict[str, Any]] = []
    source_index: dict[str, int] = {}
    for key in SECTION_KEYS:
        section = sections[key]
        local_to_global: dict[int, int] = {}
        for local_num, src in enumerate(section.get("sources", []), start=1):
            url = src.get("url", "")
            if not url:
                continue
            if url not in source_index:
                source_index[url] = len(all_sources) + 1
                all_sources.append({"number": source_index[url], **src})
            local_to_global[local_num] = source_index[url]
        text = section.get("text", "")
        for local_num, global_num in sorted(local_to_global.items(), reverse=True):
            text = text.replace(f"[{local_num}]", f"[[SRC{global_num}]]")
        for global_num in sorted(set(local_to_global.values())):
            text = text.replace(f"[[SRC{global_num}]]", f"[{global_num}]")
        section["text"] = text
        section["sources"] = [{"number": local_to_global[i], **src} for i, src in enumerate(section.get("sources", []), start=1) if i in local_to_global]
    return all_sources


def generate_dashboard_sync() -> dict[str, Any]:
    generated = now_kst()
    connections: dict[str, dict[str, Any]] = {}

    # Data APIs: failures are not exposed as a scary banner unless the core news API fails.
    trade = None
    try:
        trade = comtrade_trade()
        connections["comtrade"] = conn("UN Comtrade", "ok", "한국 HS 850760 수출입 연결", bool(os.getenv("COMTRADE_API_KEY", "").strip()))
    except Exception as exc:
        connections["comtrade"] = conn("UN Comtrade", "limited", safe_error(exc), bool(os.getenv("COMTRADE_API_KEY", "").strip()))

    eia = None
    try:
        eia = eia_battery_capacity()
        connections["eia"] = conn("U.S. EIA", "ok", "미국 운영 배터리 저장용량 연결", bool(os.getenv("EIA_API_KEY", "").strip()))
    except Exception as exc:
        connections["eia"] = conn("U.S. EIA", "limited", safe_error(exc), bool(os.getenv("EIA_API_KEY", "").strip()))

    world_bank = None
    try:
        world_bank = world_bank_manufacturing()
        connections["worldbank"] = conn("World Bank", "ok", "제조업 기반지표 연결")
    except Exception as exc:
        connections["worldbank"] = conn("World Bank", "limited", safe_error(exc))

    eurostat = eurostat_reference()
    connections["eurostat"] = conn("Eurostat", "ok", "EU 전기차 기반지표 참고 링크")

    alpha = None
    try:
        alpha = alpha_quotes_optional()
        if alpha:
            connections["alpha"] = conn("Alpha Vantage", "ok" if not alpha.get("partial") else "limited", f"관련 상장사 {len(alpha.get('quotes', []))}개 연결", True)
        else:
            connections["alpha"] = conn("Alpha Vantage", "disabled", "선택 API · 기본 비활성화", bool(os.getenv("ALPHAVANTAGE_API_KEY", "").strip()))
    except Exception as exc:
        connections["alpha"] = conn("Alpha Vantage", "disabled", safe_error(exc), bool(os.getenv("ALPHAVANTAGE_API_KEY", "").strip()))

    # News sections
    news_queries = {
        "DOMESTIC_NEWS": (DOMESTIC_QUERY, True, "domestic"),
        "GLOBAL_NEWS": (GLOBAL_QUERY, False, "global"),
        "POLICY": (POLICY_QUERY, None, "policy"),
        "COMPANIES": (COMPANY_QUERY, None, "companies"),
        "RECYCLING_TECH": (TECH_QUERY, None, "tech"),
    }
    news: dict[str, list[dict[str, Any]]] = {}
    gdelt_ok = 0
    for key, (query, is_domestic, section_name) in news_queries.items():
        try:
            news[key] = gdelt_articles(query, maxrecords=14, lookback_months=NEWS_LOOKBACK_MONTHS, domestic=is_domestic, section=section_name)
            if news[key]:
                gdelt_ok += 1
        except Exception:
            news[key] = []
        time.sleep(0.2)

    connections["gdelt"] = conn(
        "GDELT",
        "ok" if gdelt_ok >= 2 else ("limited" if gdelt_ok else "error"),
        f"사용후 배터리 키워드 · 최근 {NEWS_LOOKBACK_MONTHS}개월 · 뉴스영역 {gdelt_ok}/5 연결" if gdelt_ok else "뉴스 기사 응답 없음",
    )

    sections: dict[str, Any] = {}
    fallback = {
        "DOMESTIC_NEWS": "• 최근 6개월 내 국내 사용후 배터리 재활용 관련 뉴스가 조회되지 않았거나, GDELT 수집 결과가 일시적으로 제한됨",
        "GLOBAL_NEWS": "• 최근 6개월 내 해외 사용후 배터리 재활용 관련 뉴스가 조회되지 않았거나, GDELT 수집 결과가 일시적으로 제한됨",
        "POLICY": "• 최근 배터리 재생원료 인증·배터리 여권·EPR·EU Battery Regulation 관련 정책 뉴스 조회 결과 없음",
        "COMPANIES": "• 최근 사용후 배터리 재활용 기업·투자·공장 관련 뉴스 조회 결과 없음",
        "RECYCLING_TECH": "• 최근 블랙매스·습식제련·직접재활용·Li/Ni/Co 회수 관련 뉴스 조회 결과 없음",
    }
    for key in ("DOMESTIC_NEWS", "GLOBAL_NEWS", "POLICY", "COMPANIES", "RECYCLING_TECH"):
        text, srcs = news_section(news.get(key, []), fallback[key], limit=8)
        sections[key] = {**SECTION_META[key], "text": text, "sources": srcs}

    market_lines: list[str] = []
    market_sources: list[dict[str, str]] = []
    if trade:
        exp = trade.get("exports")
        imp = trade.get("imports")
        if exp:
            ch = trade.get("export_change")
            ch_text = f" ({ch:+.1f}% YoY)" if ch is not None else ""
            market_lines.append(f"• 한국 리튬이온축전지(HS 850760) 수출 — {exp['year']}년 {fmt_money(exp['value'])}{ch_text} [1]")
        if imp:
            ch = trade.get("import_change")
            ch_text = f" ({ch:+.1f}% YoY)" if ch is not None else ""
            market_lines.append(f"• 한국 리튬이온축전지(HS 850760) 수입 — {imp['year']}년 {fmt_money(imp['value'])}{ch_text} [1]")
        market_sources.append(trade["source"])
    if eia:
        n = len(market_sources) + 1
        top_states = ", ".join(f"{s} {mw/1000:.1f}GW" for s, mw in eia.get("top_states", []) if mw) or ""
        market_lines.append(f"• 미국 운영 중 utility-scale 배터리 저장용량 — {fmt_number(eia['capacity_mw'] / 1000, 2)} GW · {eia['period']} 기준 [{n}]" + (f"\n  주요 주: {top_states}" if top_states else ""))
        market_sources.append(eia["source"])
    if world_bank and world_bank.get("rows"):
        n = len(market_sources) + 1
        wb_text = " · ".join(f"{r['country']} {r['value']:.1f}%({r['year']})" for r in world_bank["rows"])
        market_lines.append(f"• 주요 배터리 경제권 제조업 부가가치 비중 — {wb_text} [{n}]")
        market_sources.append(world_bank["source"])
    if eurostat:
        n = len(market_sources) + 1
        market_lines.append(f"• EU 전기차 등록 통계 참고 — 배터리 순환·사용후 배터리 발생 기반지표로 활용 가능 [{n}]")
        market_sources.append(eurostat["source"])
    if alpha and alpha.get("quotes"):
        for quote in alpha["quotes"]:
            n = len(market_sources) + 1
            change = quote.get("change_percent")
            ch = f" · {change:+.2f}%" if change is not None else ""
            date_text = f" · {quote['latest_day']}" if quote.get("latest_day") else ""
            market_lines.append(f"• 관련 상장사 보조지표 {quote['symbol']} — ${quote['price']:,.2f}{ch}{date_text} [{n}]")
            market_sources.append(quote["source"])
    if not market_lines:
        market_lines.append("• 통계 API 연결 대기 — Render 환경변수의 COMTRADE_API_KEY, EIA_API_KEY 입력 여부 확인 필요")
    market_lines.append("\n해석 유의: 시장·기반 통계는 사용후 배터리 발생량 자체가 아니라 향후 회수·재활용 시장 규모를 추정할 때 참고하는 배터리 보급·무역·산업 기반지표임")
    sections["MARKET_STATS"] = {**SECTION_META["MARKET_STATS"], "text": "\n\n".join(market_lines), "sources": market_sources}

    all_sources = assign_global_source_numbers(sections)

    domestic_count = len(news.get("DOMESTIC_NEWS", []))
    global_count = len(news.get("GLOBAL_NEWS", []))
    kpis: list[dict[str, Any]] = [
        {"label": "국내 뉴스", "value": f"{domestic_count}건", "change": None, "meta": f"최근 {NEWS_LOOKBACK_MONTHS}개월 · 사용후 배터리", "source": "GDELT"},
        {"label": "해외 뉴스", "value": f"{global_count}건", "change": None, "meta": f"최근 {NEWS_LOOKBACK_MONTHS}개월 · recycling/passport", "source": "GDELT"},
    ]
    if trade and trade.get("exports"):
        kpis.append({"label": "한국 Li-ion 배터리 수출", "value": fmt_money(trade["exports"]["value"]), "change": trade.get("export_change"), "meta": f"{trade['exports']['year']} · HS 850760", "source": "UN Comtrade"})
    else:
        kpis.append({"label": "한국 Li-ion 배터리 수출", "value": "확인 대기", "change": None, "meta": "COMTRADE_API_KEY 확인", "source": "UN Comtrade"})
    if eia:
        kpis.append({"label": "미국 BESS 운영용량", "value": f"{fmt_number(eia['capacity_mw'] / 1000, 2)} GW", "change": None, "meta": eia["period"], "source": "U.S. EIA"})
    else:
        kpis.append({"label": "미국 BESS 운영용량", "value": "확인 대기", "change": None, "meta": "EIA_API_KEY 확인", "source": "U.S. EIA"})

    critical_issue = connections["gdelt"]["status"] == "error"
    optional_limited = [v["label"] for k, v in connections.items() if v["status"] in {"limited", "error"} and k != "gdelt"]
    status = "live" if not critical_issue else "partial"
    reason = ""
    if critical_issue:
        reason = "GDELT 뉴스 수집 확인 필요"
    elif optional_limited:
        reason = "보조 통계 일부 제한: " + ", ".join(optional_limited)

    return {
        "status": status,
        "reason": reason,
        "generated_at": generated.isoformat(),
        "expires_at": datetime.fromtimestamp(time.time() + CACHE_TTL, KST).isoformat(),
        "news_lookback_months": NEWS_LOOKBACK_MONTHS,
        "cache_ttl_seconds": CACHE_TTL,
        "keyword_catalog": KEYWORD_CATALOG,
        "sections": sections,
        "kpis": kpis,
        "all_sources": all_sources,
        "connections": connections,
        "news_counts": {
            "domestic": domestic_count,
            "global": global_count,
            "policy": len(news.get("POLICY", [])),
            "companies": len(news.get("COMPANIES", [])),
            "tech": len(news.get("RECYCLING_TECH", [])),
        },
        "environment": {
            "COMTRADE_API_KEY": bool(os.getenv("COMTRADE_API_KEY", "").strip()),
            "EIA_API_KEY": bool(os.getenv("EIA_API_KEY", "").strip()),
            "ALPHAVANTAGE_API_KEY": bool(os.getenv("ALPHAVANTAGE_API_KEY", "").strip()),
            "ENABLE_ALPHA": os.getenv("ENABLE_ALPHA", "false").strip().lower() in {"1", "true", "yes", "y"},
            "NEWS_LOOKBACK_MONTHS": NEWS_LOOKBACK_MONTHS,
        },
    }


async def generate_dashboard(force: bool = False) -> dict[str, Any]:
    if not force and _cache["data"] is not None and time.time() < _cache["expires_at"]:
        return _cache["data"]
    async with _refresh_lock:
        if not force and _cache["data"] is not None and time.time() < _cache["expires_at"]:
            return _cache["data"]
        try:
            data = await asyncio.to_thread(generate_dashboard_sync)
            _cache["data"] = data
            _cache["expires_at"] = time.time() + CACHE_TTL
            return data
        except Exception as exc:
            if _cache["data"] is not None:
                stale = dict(_cache["data"])
                stale["status"] = "stale"
                stale["reason"] = f"신규 조회 실패, 기존 캐시 표시: {safe_error(exc)}"
                return stale
            raise


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/dashboard")
async def api_dashboard(force: bool = Query(False)) -> JSONResponse:
    data = await generate_dashboard(force=force)
    return JSONResponse(data)


@app.get("/api/status")
async def api_status() -> JSONResponse:
    data = await generate_dashboard(force=False)
    return JSONResponse({
        "status": data.get("status"),
        "reason": data.get("reason"),
        "generated_at": data.get("generated_at"),
        "news_lookback_months": data.get("news_lookback_months"),
        "connections": data.get("connections"),
        "environment": data.get("environment"),
        "news_counts": data.get("news_counts"),
    })


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "used-battery-recycling-briefing-v6"}
