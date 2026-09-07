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
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "21600"))  # 6 hours
HTTP_TIMEOUT = 22
USER_AGENT = "UsedBatteryTrendDashboard/5.0 (+Render; public-data dashboard)"

app = FastAPI(title="Used Battery Circularity Briefing", version="5.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_refresh_lock = asyncio.Lock()

SECTION_KEYS = [
    "DOMESTIC_NEWS",
    "GLOBAL_NEWS",
    "DOMESTIC_POLICY",
    "GLOBAL_POLICY",
    "RECYCLING",
    "MARKET",
]

SECTION_META = {
    "DOMESTIC_NEWS": {"title": "국내 사용후 배터리 뉴스", "subtitle": "폐배터리 · 사용후 배터리 · 재활용 · 재사용"},
    "GLOBAL_NEWS": {"title": "해외 사용후 배터리 뉴스", "subtitle": "End-of-life battery · Recycling · Second life"},
    "DOMESTIC_POLICY": {"title": "국내 정책·제도", "subtitle": "회수체계 · 재활용 · 재생원료 · 인증제도"},
    "GLOBAL_POLICY": {"title": "해외 정책·규제", "subtitle": "EU Battery Regulation · EPR · Recycled content"},
    "RECYCLING": {"title": "재활용·재생원료 동향", "subtitle": "블랙매스 · 습식제련 · 재생 Li·Ni·Co · 투자"},
    "MARKET": {"title": "시장·기반 통계", "subtitle": "한국 배터리 무역 · 미국 BESS · EU 전기차 · 제조업"},
}

NEWS_LOOKBACK_MONTHS = int(os.getenv("NEWS_LOOKBACK_MONTHS", "6"))

KEYWORD_CATALOG = [
    {
        "no": 1,
        "ko": "사용후 배터리 재활용",
        "en": "End-of-life battery recycling",
        "ja": "使用済み電池リサイクル",
        "zh": "退役电池回收利用",
        "query": '("end-of-life battery recycling" OR "used battery recycling" OR "spent battery recycling")',
    },
    {
        "no": 2,
        "ko": "배터리 재생원료",
        "en": "Recycled battery materials",
        "ja": "電池再生原料",
        "zh": "电池再生原料",
        "query": '("recycled battery materials" OR "recycled battery material")',
    },
    {
        "no": 3,
        "ko": "배터리 순환경제",
        "en": "Battery circular economy",
        "ja": "電池循環経済",
        "zh": "电池循环经济",
        "query": '("battery circular economy" OR "battery circularity")',
    },
    {
        "no": 4,
        "ko": "배터리 재생원료 인증",
        "en": "Recycled battery materials certification",
        "ja": "電池再生原料認証",
        "zh": "电池再生原料认证",
        "query": '("recycled battery materials certification" OR "recycled content certification")',
    },
    {
        "no": 5,
        "ko": "배터리 여권",
        "en": "Battery passport",
        "ja": "バッテリーパスポート",
        "zh": "电池护照",
        "query": '"battery passport"',
    },
]


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


# ---------------------------------------------------------------------------
# Official / free data APIs
# ---------------------------------------------------------------------------

def comtrade_trade() -> dict[str, Any]:
    """Korea lithium-ion accumulators HS 850760 annual trade with World."""
    key = os.getenv("COMTRADE_API_KEY", "").strip()
    current_year = now_kst().year
    years = [current_year - i for i in range(1, 6)]

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
                return (
                    request_json(
                        "https://comtradeapi.un.org/data/v1/get/C/A/HS",
                        params={**params, "subscription-key": key},
                    ).get("data")
                    or []
                )
            except Exception as exc:
                errors.append(f"auth:{type(exc).__name__}")
        try:
            return (
                request_json(
                    "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
                    params=params,
                ).get("data")
                or []
            )
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
                except (TypeError, ValueError):
                    ref_year = year
                return {"year": ref_year, "value": value}
        return None

    by_flow: dict[str, list[dict[str, Any]]] = {"X": [], "M": []}
    for flow in ("X", "M"):
        for year in years:
            try:
                item = normalize(fetch_one(year, flow), year)
            except Exception:
                item = None
            if item:
                by_flow[flow].append(item)
            if len(by_flow[flow]) >= 2:
                break

    exports = sorted(by_flow["X"], key=lambda x: x["year"], reverse=True)
    imports = sorted(by_flow["M"], key=lambda x: x["year"], reverse=True)
    if not exports and not imports:
        raise RuntimeError("UN Comtrade에서 한국 HS 850760 최근 연도 데이터를 찾지 못함")

    export_latest = exports[0] if exports else None
    import_latest = imports[0] if imports else None
    export_prev = exports[1] if len(exports) > 1 else None
    import_prev = imports[1] if len(imports) > 1 else None
    latest_year = max([x["year"] for x in (export_latest, import_latest) if x] or years[:1])
    src_url = build_url(
        "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
        {
            "reporterCode": "410",
            "partnerCode": "0",
            "cmdCode": "850760",
            "flowCode": "X",
            "period": str(latest_year),
        },
    )

    return {
        "exports": export_latest,
        "imports": import_latest,
        "export_prev": export_prev,
        "import_prev": import_prev,
        "export_change": pct_change(
            export_latest["value"] if export_latest else None,
            export_prev["value"] if export_prev else None,
        ),
        "import_change": pct_change(
            import_latest["value"] if import_latest else None,
            import_prev["value"] if import_prev else None,
        ),
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
    latest_rows = latest_payload.get("response", {}).get("data", [])
    if not latest_rows:
        raise RuntimeError("EIA 배터리 저장용량 최신 기간을 찾지 못함")
    period = str(latest_rows[0].get("period", ""))
    if not period:
        raise RuntimeError("EIA 최신 기간 값 없음")

    payload = request_json(
        base,
        params={**common, "start": period, "end": period, "length": "5000", "offset": "0"},
    )
    rows = payload.get("response", {}).get("data", [])
    total_mw = 0.0
    count = 0
    states: dict[str, float] = {}
    for row in rows:
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
        "source": source(
            "U.S. EIA · Inventory of Operable Generators",
            "https://www.eia.gov/opendata/browser/electricity/operating-generator-capacity",
        ),
    }


def alpha_quotes() -> dict[str, Any]:
    """Free Global Quote with a deliberate delay to respect free rate limits."""
    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY 미설정")

    symbols = [
        s.strip().upper()
        for s in os.getenv("STOCK_SYMBOLS", "ALB,SQM,TSLA").split(",")
        if s.strip()
    ][:3]
    out: list[dict[str, Any]] = []
    messages: list[str] = []

    for idx, symbol in enumerate(symbols):
        if idx:
            time.sleep(1.25)  # Alpha Vantage free tier: space requests out
        payload = request_json(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key},
        )
        quote = payload.get("Global Quote") or {}
        if not quote:
            msg = str(
                payload.get("Information")
                or payload.get("Note")
                or payload.get("Error Message")
                or "응답 데이터 없음"
            )
            messages.append(f"{symbol}: {msg[:120]}")
            continue
        price = safe_float(quote.get("05. price"))
        if price is None:
            continue
        out.append(
            {
                "symbol": symbol,
                "price": price,
                "change_percent": safe_float(quote.get("10. change percent")),
                "latest_day": quote.get("07. latest trading day") or "",
                "source": source(
                    f"Alpha Vantage · {symbol} Global Quote",
                    "https://www.alphavantage.co/documentation/#latestprice",
                ),
            }
        )

    if not out:
        detail = " / ".join(messages) if messages else "무료 호출 한도 또는 API 키 상태 확인 필요"
        raise RuntimeError("Alpha Vantage: " + detail[:220])
    return {"quotes": out, "partial": len(out) < len(symbols), "messages": messages}


def _jsonstat_categories(payload: dict[str, Any], dim_id: str) -> list[tuple[str, str, int]]:
    dim = (payload.get("dimension") or {}).get(dim_id) or {}
    cat = dim.get("category") or {}
    index = cat.get("index") or {}
    labels = cat.get("label") or {}
    if isinstance(index, list):
        return [(str(code), str(labels.get(code, code)), pos) for pos, code in enumerate(index)]
    if isinstance(index, dict):
        return sorted(
            [(str(code), str(labels.get(code, code)), int(pos)) for code, pos in index.items()],
            key=lambda x: x[2],
        )
    return []


def _jsonstat_value_items(values: Any) -> list[tuple[int, float]]:
    """JSON-stat may expose value as a dense list or a sparse {index:value} object."""
    out: list[tuple[int, float]] = []
    if isinstance(values, dict):
        iterable = values.items()
    elif isinstance(values, list):
        iterable = enumerate(values)
    else:
        return out
    for raw_idx, raw_val in iterable:
        if raw_val is None:
            continue
        try:
            out.append((int(raw_idx), float(raw_val)))
        except (TypeError, ValueError):
            continue
    return out


def eurostat_ev_registrations() -> dict[str, Any]:
    """Latest EU battery-only electric passenger-car new registrations."""
    base = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/road_eqr_carpda"
    payload = request_json(base, params={"lang": "en", "geo": "EU27_2020", "lastTimePeriod": "1"})
    ids = payload.get("id") or []
    sizes = payload.get("size") or []
    value_items = _jsonstat_value_items(payload.get("value"))
    if not ids or not sizes or not value_items:
        raise RuntimeError("Eurostat road_eqr_carpda 응답 형식 확인 필요")

    cats = {dim: _jsonstat_categories(payload, dim) for dim in ids}
    strides = []
    for i in range(len(sizes)):
        stride = 1
        for later in sizes[i + 1 :]:
            stride *= int(later)
        strides.append(stride)

    preferred: dict[str, set[int]] = {}
    for dim in ids:
        entries = cats.get(dim, [])
        if dim == "geo":
            m = {pos for code, label, pos in entries if code == "EU27_2020" or "27 countries" in label.lower()}
            if m:
                preferred[dim] = m
        elif dim == "freq":
            m = {pos for code, label, pos in entries if code == "A" or label.lower() == "annual"}
            if m:
                preferred[dim] = m
        elif dim == "unit":
            m = {pos for code, label, pos in entries if code == "NR" or "number" in label.lower()}
            if m:
                preferred[dim] = m
        else:
            m = {
                pos
                for code, label, pos in entries
                if "battery-only" in label.lower()
                or "battery only" in label.lower()
                or "battery electric" in label.lower()
            }
            if m:
                preferred[dim] = m

    candidates: list[tuple[float, list[int]]] = []
    for flat_index, val in value_items:
        coords: list[int] = []
        remain = flat_index
        for size, stride in zip(sizes, strides):
            pos = remain // stride
            remain %= stride
            coords.append(int(pos))
        if all(
            preferred.get(dim) is None or coords[i] in preferred[dim]
            for i, dim in enumerate(ids)
        ):
            candidates.append((val, coords))

    if not candidates:
        raise RuntimeError("Eurostat EU battery-only 승용차 등록값을 찾지 못함")
    value, coords = max(candidates, key=lambda x: x[0])

    codes: dict[str, str] = {}
    for i, dim in enumerate(ids):
        entries = cats.get(dim, [])
        match = next(((code, label) for code, label, pos in entries if pos == coords[i]), ("", ""))
        codes[dim] = match[0]

    try:
        year = int(str(codes.get("time") or now_kst().year - 1)[:4])
    except (TypeError, ValueError):
        year = now_kst().year - 1

    if value < 100_000:
        raise RuntimeError(f"Eurostat 선택값이 비정상적으로 작음 ({value:g})")

    return {
        "year": year,
        "value": value,
        "source": source(
            "Eurostat · New passenger cars by type of motor energy",
            "https://ec.europa.eu/eurostat/databrowser/view/road_eqr_carpda/default/table?lang=en",
        ),
    }


def world_bank_manufacturing() -> dict[str, Any]:
    countries = "KOR;CHN;USA;EUU"
    indicator = "NV.IND.MANF.ZS"
    end_year = now_kst().year
    start_year = end_year - 6
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
    payload = request_json(
        url,
        params={"format": "json", "date": f"{start_year}:{end_year}", "per_page": "200"},
    )
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
        except (TypeError, ValueError):
            continue
        if code not in latest or year > latest[code]["year"]:
            latest[code] = {"country": wanted[code], "year": year, "value": value}
    if not latest:
        raise RuntimeError("World Bank 제조업 지표 값 없음")

    return {
        "rows": [latest[c] for c in ("KOR", "CHN", "USA", "EUU") if c in latest],
        "source": source(
            "World Bank · Manufacturing, value added (% of GDP)",
            "https://data.worldbank.org/indicator/NV.IND.MANF.ZS",
        ),
    }


# ---------------------------------------------------------------------------
# GDELT news — specifically used / end-of-life batteries and recycling
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


def _shift_months(dt: datetime, months: int) -> datetime:
    """Shift an aware datetime by whole calendar months."""
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _gdelt_fetch_window(query: str, start_dt: datetime, end_dt: datetime, maxrecords: int) -> list[dict[str, Any]]:
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


def gdelt_articles(
    queries: str | list[str],
    *,
    maxrecords: int = 12,
    lookback_months: int = NEWS_LOOKBACK_MONTHS,
) -> list[dict[str, Any]]:
    """
    Search the requested period by splitting it into <=3-month windows.
    GDELT ArticleList prioritizes the most recent 3 months of a broad window,
    so splitting the default 6-month horizon makes the full period searchable.
    """
    query_list = [queries] if isinstance(queries, str) else queries
    now_utc = datetime.now(timezone.utc)
    start_utc = _shift_months(now_utc, -max(1, lookback_months))

    windows: list[tuple[datetime, datetime]] = []
    cursor_end = now_utc
    while cursor_end > start_utc:
        cursor_start = max(_shift_months(cursor_end, -3), start_utc)
        windows.append((cursor_start, cursor_end))
        cursor_end = cursor_start - timedelta(seconds=1)

    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    last_error: Exception | None = None

    # Fetch a little more from each window so older coverage is not discarded
    # simply because the most recent window already has many results.
    per_window = min(max(maxrecords * 2, 20), 75)
    for window_start, window_end in windows:
        for query in query_list:
            try:
                articles = _gdelt_fetch_window(query, window_start, window_end, per_window)
            except Exception as exc:
                last_error = exc
                continue
            for article in articles:
                if not isinstance(article, dict):
                    continue
                url = str(article.get("url") or "").strip()
                title = str(article.get("title") or "").strip()
                if not url or not title or url in seen_urls:
                    continue
                seen_urls.add(url)
                collected.append(
                    {
                        "title": title,
                        "url": url,
                        "domain": article.get("domain") or "",
                        "date": parse_gdelt_date(article.get("seendate") or ""),
                        "country": article.get("sourcecountry") or "",
                        "language": article.get("language") or "",
                    }
                )
        time.sleep(0.12)

    collected.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True)
    if not collected and last_error:
        raise RuntimeError(f"GDELT 호출 실패: {type(last_error).__name__}")
    return collected[:maxrecords]


def news_section(articles: list[dict[str, Any]], fallback: str, *, limit: int = 7) -> tuple[str, list[dict[str, str]]]:
    if not articles:
        return fallback, []
    lines: list[str] = []
    sources: list[dict[str, str]] = []
    for i, article in enumerate(articles[:limit], start=1):
        meta = " · ".join(
            x for x in [article.get("domain"), article.get("country"), article.get("date")] if x
        )
        lines.append(f"• {article['title']} [{i}]" + (f"\n  {meta}" if meta else ""))
        sources.append(source(article["title"], article["url"]))
    return "\n\n".join(lines), sources


def assign_global_source_numbers(sections: dict[str, Any]) -> list[dict[str, Any]]:
    all_sources: list[dict[str, Any]] = []
    source_index: dict[str, int] = {}
    for key in SECTION_KEYS:
        section = sections[key]
        local_sources = section.get("sources", [])
        local_to_global: dict[int, int] = {}
        for local_num, src in enumerate(local_sources, start=1):
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
        section["sources"] = [
            {"number": local_to_global[i], **src}
            for i, src in enumerate(local_sources, start=1)
            if i in local_to_global
        ]
    return all_sources


def _conn(label: str, status: str, detail: str = "", configured: bool | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"status": status, "label": label, "detail": detail}
    if configured is not None:
        item["configured"] = configured
    return item


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:220] if text else type(exc).__name__


def generate_dashboard_sync() -> dict[str, Any]:
    generated = now_kst()
    connections: dict[str, dict[str, Any]] = {}

    # Official data APIs ------------------------------------------------------
    trade = None
    try:
        trade = comtrade_trade()
        connections["comtrade"] = _conn(
            "UN Comtrade", "ok", "한국 HS 850760 수출입 연결", bool(os.getenv("COMTRADE_API_KEY", "").strip())
        )
    except Exception as exc:
        connections["comtrade"] = _conn(
            "UN Comtrade", "error", _safe_error(exc), bool(os.getenv("COMTRADE_API_KEY", "").strip())
        )

    eia = None
    try:
        eia = eia_battery_capacity()
        connections["eia"] = _conn("U.S. EIA", "ok", "미국 운영 배터리 저장용량 연결", True)
    except Exception as exc:
        connections["eia"] = _conn(
            "U.S. EIA", "error", _safe_error(exc), bool(os.getenv("EIA_API_KEY", "").strip())
        )

    alpha = None
    try:
        alpha = alpha_quotes()
        alpha_status = "limited" if alpha.get("partial") else "ok"
        detail = f"상장사 {len(alpha.get('quotes', []))}개 종가 지표 연결"
        if alpha_status == "limited":
            detail += " · 일부 무료 호출 제한"
        connections["alpha"] = _conn("Alpha Vantage", alpha_status, detail, True)
    except Exception as exc:
        connections["alpha"] = _conn(
            "Alpha Vantage", "limited", _safe_error(exc), bool(os.getenv("ALPHAVANTAGE_API_KEY", "").strip())
        )

    eurostat = None
    try:
        eurostat = eurostat_ev_registrations()
        connections["eurostat"] = _conn("Eurostat", "ok", "EU battery-only 승용차 등록 연결")
    except Exception as exc:
        connections["eurostat"] = _conn("Eurostat", "limited", _safe_error(exc))

    world_bank = None
    try:
        world_bank = world_bank_manufacturing()
        connections["worldbank"] = _conn("World Bank", "ok", "제조업 부가가치 지표 연결")
    except Exception as exc:
        connections["worldbank"] = _conn("World Bank", "limited", _safe_error(exc))

    # Used-battery news -------------------------------------------------------
    # User-defined five keyword families. GDELT searches translated global news,
    # while the dashboard displays the Korean/English/Japanese/Chinese keyword dictionary.
    all_keyword_query = "(" + " OR ".join(item["query"] for item in KEYWORD_CATALOG) + ")"
    recycling_keyword_query = "(" + " OR ".join(item["query"] for item in KEYWORD_CATALOG[:3]) + ")"
    policy_keyword_query = "(" + " OR ".join(item["query"] for item in KEYWORD_CATALOG[3:]) + ")"

    domestic_query = f"{all_keyword_query} sourcecountry:southkorea"
    global_query = f"{all_keyword_query} -sourcecountry:southkorea"
    domestic_policy_query = f"{policy_keyword_query} sourcecountry:southkorea"
    global_policy_query = f"{policy_keyword_query} -sourcecountry:southkorea"
    recycling_market_query = recycling_keyword_query

    news: dict[str, list[dict[str, Any]]] = {}
    news_queries = {
        "DOMESTIC_NEWS": domestic_query,
        "GLOBAL_NEWS": global_query,
        "DOMESTIC_POLICY": domestic_policy_query,
        "GLOBAL_POLICY": global_policy_query,
        "RECYCLING": recycling_market_query,
    }
    gdelt_ok = 0
    for key, query in news_queries.items():
        try:
            news[key] = gdelt_articles(query, maxrecords=12, lookback_months=NEWS_LOOKBACK_MONTHS)
            if news[key]:
                gdelt_ok += 1
        except Exception:
            news[key] = []
        time.sleep(0.25)

    connections["gdelt"] = _conn(
        "GDELT",
        "ok" if gdelt_ok >= 2 else ("limited" if gdelt_ok else "error"),
        f"5개 핵심키워드 · 최근 {NEWS_LOOKBACK_MONTHS}개월 · 뉴스 카테고리 {gdelt_ok}/5 연결"
        if gdelt_ok
        else "GDELT 기사 목록 응답 없음",
    )

    # Build sections ---------------------------------------------------------
    sections: dict[str, Any] = {}
    fallback = {
        "DOMESTIC_NEWS": "• 최근 6개월 내 국내 5개 핵심키워드 관련 뉴스가 조회되지 않았거나 GDELT가 일시적으로 제한됨",
        "GLOBAL_NEWS": "• 최근 6개월 내 해외 5개 핵심키워드 관련 뉴스가 조회되지 않았거나 GDELT가 일시적으로 제한됨",
        "DOMESTIC_POLICY": "• 최근 국내 사용후 배터리 회수·재활용·재생원료 정책 뉴스 조회 결과 없음",
        "GLOBAL_POLICY": "• 최근 해외 배터리 재활용·재생원료·EPR·배터리여권 규제 뉴스 조회 결과 없음",
        "RECYCLING": "• 최근 블랙매스·습식제련·재생 Li·Ni·Co 관련 글로벌 동향 조회 결과 없음",
    }
    for key in ("DOMESTIC_NEWS", "GLOBAL_NEWS", "DOMESTIC_POLICY", "GLOBAL_POLICY", "RECYCLING"):
        text, srcs = news_section(news.get(key, []), fallback[key], limit=7)
        sections[key] = {**SECTION_META[key], "text": text, "sources": srcs}

    market_lines: list[str] = []
    market_sources: list[dict[str, str]] = []
    if trade:
        exp = trade.get("exports")
        imp = trade.get("imports")
        if exp:
            ch = trade.get("export_change")
            ch_text = f" ({ch:+.1f}% YoY)" if ch is not None else ""
            market_lines.append(
                f"• 한국 리튬이온축전지(HS 850760) 수출 — {exp['year']}년 {fmt_money(exp['value'])}{ch_text} [1]"
            )
        if imp:
            ch = trade.get("import_change")
            ch_text = f" ({ch:+.1f}% YoY)" if ch is not None else ""
            market_lines.append(
                f"• 한국 리튬이온축전지(HS 850760) 수입 — {imp['year']}년 {fmt_money(imp['value'])}{ch_text} [1]"
            )
        market_sources.append(trade["source"])

    if eia:
        local_num = len(market_sources) + 1
        market_lines.append(
            f"• 미국 운영 중 utility-scale 배터리 저장용량 — {fmt_number(eia['capacity_mw'] / 1000, 2)} GW · {eia['period']} 기준 [{local_num}]"
        )
        market_sources.append(eia["source"])

    if eurostat:
        local_num = len(market_sources) + 1
        market_lines.append(
            f"• EU 신규 battery-only 전기 승용차 등록 — {fmt_number(eurostat['value'] / 1_000_000, 2)}백만 대 · {eurostat['year']}년 [{local_num}]"
        )
        market_sources.append(eurostat["source"])

    if world_bank and world_bank.get("rows"):
        local_num = len(market_sources) + 1
        wb_text = " · ".join(f"{r['country']} {r['value']:.1f}%({r['year']})" for r in world_bank["rows"])
        market_lines.append(f"• 주요 배터리 경제권 제조업 부가가치 비중 — {wb_text} [{local_num}]")
        market_sources.append(world_bank["source"])

    if alpha and alpha.get("quotes"):
        for quote in alpha["quotes"]:
            local_num = len(market_sources) + 1
            change = quote.get("change_percent")
            change_text = f" · {change:+.2f}%" if change is not None else ""
            date_text = f" · {quote['latest_day']}" if quote.get("latest_day") else ""
            market_lines.append(
                f"• 관련 시장지표 {quote['symbol']} — ${quote['price']:,.2f}{change_text}{date_text} [{local_num}]"
            )
            market_sources.append(quote["source"])

    if not market_lines:
        market_lines.append("• 공식 통계 데이터 연결 대기 — Render 환경변수의 무료 API 키 설정 확인 필요")
    market_lines.append(
        "\n해석 유의: 위 지표는 사용후 배터리 발생량 자체가 아니라 향후 회수·재활용 시장의 기반이 되는 배터리 보급·무역·산업 지표임"
    )
    sections["MARKET"] = {
        **SECTION_META["MARKET"],
        "text": "\n\n".join(market_lines),
        "sources": market_sources,
    }

    all_sources = assign_global_source_numbers(sections)

    # KPI: user-facing focus is news + used-battery market base
    domestic_count = len(news.get("DOMESTIC_NEWS", []))
    global_count = len(news.get("GLOBAL_NEWS", []))
    kpis: list[dict[str, Any]] = [
        {
            "label": "국내 사용후 배터리 뉴스",
            "value": f"{domestic_count}건",
            "change": None,
            "meta": f"최근 {NEWS_LOOKBACK_MONTHS}개월 · 5개 핵심키워드",
            "source": "GDELT",
        },
        {
            "label": "해외 사용후 배터리 뉴스",
            "value": f"{global_count}건",
            "change": None,
            "meta": f"최근 {NEWS_LOOKBACK_MONTHS}개월 · 글로벌 뉴스",
            "source": "GDELT",
        },
    ]
    if trade and trade.get("exports"):
        exp = trade["exports"]
        kpis.append(
            {
                "label": "한국 Li-ion 배터리 수출",
                "value": fmt_money(exp["value"]),
                "change": trade.get("export_change"),
                "meta": f"{exp['year']} · HS 850760",
                "source": "UN Comtrade",
            }
        )
    else:
        kpis.append({"label": "한국 Li-ion 배터리 수출", "value": "연결 대기", "change": None, "meta": "HS 850760", "source": ""})

    if eia:
        kpis.append(
            {
                "label": "미국 BESS 운영용량",
                "value": f"{fmt_number(eia['capacity_mw'] / 1000, 2)} GW",
                "change": None,
                "meta": f"{eia['period']} · 향후 사용후 배터리 기반",
                "source": "U.S. EIA",
            }
        )
    else:
        kpis.append({"label": "미국 BESS 운영용량", "value": "연결 대기", "change": None, "meta": "utility-scale", "source": ""})

    # Main status: GDELT + the two key official feeds are essential; the rest are supplemental.
    essential_ok = (
        connections.get("gdelt", {}).get("status") in {"ok", "limited"}
        and connections.get("comtrade", {}).get("status") == "ok"
        and connections.get("eia", {}).get("status") == "ok"
    )
    status = "live" if essential_ok else "partial"
    warning_sources = [
        item["label"]
        for item in connections.values()
        if item.get("status") in {"error", "limited"}
    ]
    expires = generated.timestamp() + CACHE_TTL

    return {
        "status": status,
        "reason": "" if not warning_sources else "보조 API 확인: " + ", ".join(warning_sources),
        "generated_at": generated.isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires, KST).isoformat(timespec="seconds"),
        "cache_seconds": CACHE_TTL,
        "sections": sections,
        "all_sources": all_sources,
        "kpis": kpis[:4],
        "connections": connections,
        "news_counts": {"domestic": domestic_count, "global": global_count},
        "news_lookback_months": NEWS_LOOKBACK_MONTHS,
        "keywords": KEYWORD_CATALOG,
    }


async def get_dashboard() -> dict[str, Any]:
    now_ts = time.time()
    if _cache["data"] is not None and now_ts < _cache["expires_at"]:
        return _cache["data"]

    async with _refresh_lock:
        now_ts = time.time()
        if _cache["data"] is not None and now_ts < _cache["expires_at"]:
            return _cache["data"]
        try:
            data = await asyncio.to_thread(generate_dashboard_sync)
            _cache["data"] = data
            _cache["expires_at"] = now_ts + CACHE_TTL
            return data
        except Exception:
            if _cache["data"] is not None:
                stale = dict(_cache["data"])
                stale["status"] = "stale"
                stale["reason"] = "외부 무료 API 호출 중 일시적 오류가 발생하여 마지막 정상 데이터를 표시함"
                return stale
            return {
                "status": "offline",
                "reason": "외부 무료 API 호출에 실패함. Render 환경변수와 API 무료 사용한도를 확인할 필요가 있음",
                "generated_at": now_kst().isoformat(timespec="seconds"),
                "expires_at": now_kst().isoformat(timespec="seconds"),
                "cache_seconds": CACHE_TTL,
                "sections": {
                    key: {**SECTION_META[key], "text": "데이터 연결 대기", "sources": []}
                    for key in SECTION_KEYS
                },
                "all_sources": [],
                "kpis": [],
                "connections": {},
                "news_counts": {"domestic": 0, "global": 0},
            }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/dashboard")
async def dashboard_api() -> dict[str, Any]:
    return await get_dashboard()


@app.get("/api/status")
async def status_api() -> dict[str, Any]:
    data = await get_dashboard()
    return {
        "status": data.get("status"),
        "generated_at": data.get("generated_at"),
        "news_counts": data.get("news_counts", {}),
        "connections": data.get("connections", {}),
        "environment": {
            "COMTRADE_API_KEY": bool(os.getenv("COMTRADE_API_KEY", "").strip()),
            "EIA_API_KEY": bool(os.getenv("EIA_API_KEY", "").strip()),
            "ALPHAVANTAGE_API_KEY": bool(os.getenv("ALPHAVANTAGE_API_KEY", "").strip()),
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "time": now_kst().isoformat(timespec="seconds")}
