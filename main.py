import asyncio
import os
import time
from datetime import datetime
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
USER_AGENT = "BatteryTrendDashboard/2.0 (+Render; public-data dashboard)"

app = FastAPI(title="Battery Trend Briefing", version="2.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_refresh_lock = asyncio.Lock()

SECTION_KEYS = ["MARKET", "MATERIALS", "POLICY", "RECYCLING", "COMPANIES", "TECHNOLOGY"]
SECTION_META = {
    "MARKET": {"title": "시장·수요", "subtitle": "배터리 무역 · 미국 ESS"},
    "MATERIALS": {"title": "핵심광물·소재", "subtitle": "Li · Ni · Co · 흑연 · 양극재"},
    "POLICY": {"title": "정책·규제", "subtitle": "EU · 미국 · 한국 · 중국"},
    "RECYCLING": {"title": "재활용·재생원료", "subtitle": "폐배터리 · 블랙매스 · 재생원료"},
    "COMPANIES": {"title": "기업 동향", "subtitle": "주요 상장사 지표 · 기업 뉴스"},
    "TECHNOLOGY": {"title": "기술 동향", "subtitle": "전고체 · LFP · Na-ion · 건식전극"},
}


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


def comtrade_trade() -> dict[str, Any]:
    """Korea (410) lithium-ion accumulators HS 850760, annual trade with world."""
    key = os.getenv("COMTRADE_API_KEY", "").strip()
    current_year = now_kst().year
    years = [str(y) for y in range(current_year - 1, current_year - 5, -1)]
    period = ",".join(years)
    base = "https://comtradeapi.un.org/data/v1/get/C/A/HS" if key else "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
    headers = {"Ocp-Apim-Subscription-Key": key} if key else None

    rows_by_flow: dict[str, list[dict[str, Any]]] = {}
    for flow in ("X", "M"):
        params = {
            "reporterCode": "410",
            "partnerCode": "0",
            "cmdCode": "850760",
            "flowCode": flow,
            "period": period,
            "maxrecords": "50",
            "includeDesc": "true",
        }
        payload = request_json(base, params=params, headers=headers)
        rows = payload.get("data") or payload.get("results") or []
        rows_by_flow[flow] = rows if isinstance(rows, list) else []

    def normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            year = row.get("refYear") or row.get("period")
            try:
                year = int(str(year)[:4])
            except (TypeError, ValueError):
                continue
            value = safe_float(row.get("primaryValue"))
            if value is None:
                continue
            out.append({"year": year, "value": value})
        out.sort(key=lambda x: x["year"], reverse=True)
        return out

    exports = normalize(rows_by_flow["X"])
    imports = normalize(rows_by_flow["M"])
    export_latest = exports[0] if exports else None
    import_latest = imports[0] if imports else None
    export_prev = exports[1] if len(exports) > 1 else None
    import_prev = imports[1] if len(imports) > 1 else None

    public_params = {
        "reporterCode": "410",
        "partnerCode": "0",
        "cmdCode": "850760",
        "flowCode": "X,M",
        "period": period,
    }
    src_url = build_url("https://comtradeapi.un.org/public/v1/preview/C/A/HS", public_params)

    return {
        "exports": export_latest,
        "imports": import_latest,
        "export_prev": export_prev,
        "import_prev": import_prev,
        "export_change": pct_change(export_latest["value"] if export_latest else None, export_prev["value"] if export_prev else None),
        "import_change": pct_change(import_latest["value"] if import_latest else None, import_prev["value"] if import_prev else None),
        "source": source("UN Comtrade · Korea HS 850760", src_url),
        "using_key": bool(key),
    }


def eia_battery_capacity() -> dict[str, Any]:
    """Latest U.S. utility-scale operating battery storage (MWH) nameplate capacity."""
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
    latest_params = {**common, "length": "1", "offset": "0"}
    latest_payload = request_json(base, params=latest_params)
    latest_rows = latest_payload.get("response", {}).get("data", [])
    if not latest_rows:
        raise RuntimeError("EIA 배터리 저장용량 최신 기간을 찾지 못함")
    period = str(latest_rows[0].get("period", ""))
    if not period:
        raise RuntimeError("EIA 최신 기간 값 없음")

    data_params = {
        **common,
        "start": period,
        "end": period,
        "length": "5000",
        "offset": "0",
    }
    payload = request_json(base, params=data_params)
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

    top_states = sorted(states.items(), key=lambda x: x[1], reverse=True)[:3]
    return {
        "period": period,
        "capacity_mw": total_mw,
        "generator_count": count,
        "top_states": top_states,
        "source": source(
            "U.S. EIA · Inventory of Operable Generators",
            "https://www.eia.gov/opendata/browser/electricity/operating-generator-capacity",
        ),
    }


def alpha_quotes() -> list[dict[str, Any]]:
    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY 미설정")

    symbols = [s.strip().upper() for s in os.getenv("STOCK_SYMBOLS", "ALB,SQM,TSLA").split(",") if s.strip()][:3]
    out = []
    for symbol in symbols:
        payload = request_json(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key},
        )
        quote = payload.get("Global Quote") or {}
        if not quote:
            # Free-tier limit or informational response; skip without exposing key/URL.
            continue
        price = safe_float(quote.get("05. price"))
        change_pct = safe_float(quote.get("10. change percent"))
        latest_day = quote.get("07. latest trading day") or ""
        if price is None:
            continue
        out.append({
            "symbol": symbol,
            "price": price,
            "change_percent": change_pct,
            "latest_day": latest_day,
            "source": source(
                f"Alpha Vantage · {symbol} Global Quote",
                "https://www.alphavantage.co/documentation/#latestprice",
            ),
        })
    if not out:
        raise RuntimeError("Alpha Vantage 무료 호출 한도 또는 데이터 응답 확인 필요")
    return out



def _jsonstat_categories(payload: dict[str, Any], dim_id: str) -> list[tuple[str, str, int]]:
    dim = (payload.get("dimension") or {}).get(dim_id) or {}
    cat = dim.get("category") or {}
    index = cat.get("index") or {}
    labels = cat.get("label") or {}
    if isinstance(index, list):
        return [(str(code), str(labels.get(code, code)), pos) for pos, code in enumerate(index)]
    if isinstance(index, dict):
        return sorted([(str(code), str(labels.get(code, code)), int(pos)) for code, pos in index.items()], key=lambda x: x[2])
    return []


def eurostat_ev_registrations() -> dict[str, Any]:
    """Latest EU zero-emission/battery-only passenger-car registrations from Eurostat. No API key required."""
    base = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/road_eqr_zev"
    payload = request_json(base, params={"lang": "en", "geo": "EU27_2020"})
    ids = payload.get("id") or []
    sizes = payload.get("size") or []
    values = payload.get("value") or []
    if not ids or not sizes or not values:
        raise RuntimeError("Eurostat road_eqr_zev 응답 형식 확인 필요")

    cats = {dim: _jsonstat_categories(payload, dim) for dim in ids}
    time_dim = "time" if "time" in ids else ids[-1]
    time_codes = cats.get(time_dim, [])
    if not time_codes:
        raise RuntimeError("Eurostat 시간축 없음")

    def year_key(item: tuple[str, str, int]) -> int:
        code, label, _ = item
        for candidate in (code, label):
            try:
                return int(str(candidate)[:4])
            except ValueError:
                pass
        return -1

    latest_time = max(time_codes, key=year_key)
    target_year = year_key(latest_time)

    preferred: dict[str, set[int]] = {}
    for dim in ids:
        entries = cats.get(dim, [])
        if dim == time_dim:
            preferred[dim] = {latest_time[2]}
            continue
        if dim == "geo":
            matches = {pos for code, label, pos in entries if code == "EU27_2020" or label.strip().lower() in {"european union - 27 countries (from 2020)", "european union"}}
            if matches:
                preferred[dim] = matches
                continue
        if dim in {"freq"} and any(code == "A" for code, _, _ in entries):
            preferred[dim] = {pos for code, _, pos in entries if code == "A"}
            continue
        if dim in {"unit"} and any(code == "NR" for code, _, _ in entries):
            preferred[dim] = {pos for code, _, pos in entries if code == "NR"}
            continue
        passenger = {pos for _, label, pos in entries if "passenger car" in label.lower()}
        if passenger:
            preferred[dim] = passenger
            continue
        battery = {pos for _, label, pos in entries if "battery-only" in label.lower() or "battery only" in label.lower()}
        if battery:
            preferred[dim] = battery
            continue

    # JSON-stat flattened array: last dimension varies fastest.
    strides = []
    for i in range(len(sizes)):
        stride = 1
        for later in sizes[i + 1:]:
            stride *= int(later)
        strides.append(stride)

    candidates: list[tuple[float, list[int]]] = []
    for flat_index, raw in enumerate(values):
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        coords = []
        remain = flat_index
        for size, stride in zip(sizes, strides):
            pos = remain // stride
            remain = remain % stride
            coords.append(int(pos))
        ok = True
        for i, dim in enumerate(ids):
            allowed = preferred.get(dim)
            if allowed is not None and coords[i] not in allowed:
                ok = False
                break
        if ok:
            candidates.append((val, coords))

    if not candidates:
        raise RuntimeError("Eurostat EU 승용차 등록값을 찾지 못함")
    # If an extra unspecified dimension remains, the aggregate/passenger-car value is normally the largest relevant value.
    value, coords = max(candidates, key=lambda x: x[0])

    labels = {}
    for i, dim in enumerate(ids):
        entries = cats.get(dim, [])
        match = next((label for code, label, pos in entries if pos == coords[i]), "")
        labels[dim] = match

    return {
        "year": target_year,
        "value": value,
        "label": next((v for k, v in labels.items() if "passenger car" in v.lower()), "EU zero-emission passenger cars"),
        "source": source("Eurostat · New zero-emission road vehicles (road_eqr_zev)", "https://ec.europa.eu/eurostat/databrowser/view/road_eqr_zev/default/table?lang=en"),
    }


def world_bank_manufacturing() -> dict[str, Any]:
    """Manufacturing value added (% of GDP) for key battery economies. No API key required."""
    countries = "KOR;CHN;USA;EUU"
    indicator = "NV.IND.MANF.ZS"
    end_year = now_kst().year
    start_year = end_year - 6
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
    payload = request_json(url, params={"format": "json", "date": f"{start_year}:{end_year}", "per_page": "200"})
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise RuntimeError("World Bank 응답 형식 확인 필요")
    wanted = {"KOR": "한국", "CHN": "중국", "USA": "미국", "EUU": "EU"}
    latest: dict[str, dict[str, Any]] = {}
    for row in payload[1]:
        if not isinstance(row, dict):
            continue
        code = ((row.get("countryiso3code") or "").upper())
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
        "source": source("World Bank · Manufacturing, value added (% of GDP)", "https://data.worldbank.org/indicator/NV.IND.MANF.ZS"),
    }

def parse_gdelt_date(value: str) -> str:
    if not value:
        return ""
    value = str(value)
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value[:10]


def gdelt_articles(query: str, *, timespan: str = "7d", maxrecords: int = 6) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(maxrecords),
        "timespan": timespan,
        "sort": "datedesc",
        "format": "json",
    }
    payload = request_json("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
    articles = payload.get("articles") or []
    out = []
    seen_urls = set()
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = article.get("url")
        title = (article.get("title") or "").strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({
            "title": title,
            "url": url,
            "domain": article.get("domain") or "",
            "date": parse_gdelt_date(article.get("seendate") or ""),
            "country": article.get("sourcecountry") or "",
        })
        if len(out) >= maxrecords:
            break
    return out


def news_section(articles: list[dict[str, Any]], fallback: str) -> tuple[str, list[dict[str, str]]]:
    if not articles:
        return fallback, []
    lines = []
    sources = []
    for i, article in enumerate(articles[:4], start=1):
        meta = " · ".join(x for x in [article.get("domain"), article.get("date")] if x)
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
        # Replace local [1], [2] markers from right to left through a temporary token.
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


def generate_dashboard_sync() -> dict[str, Any]:
    generated = now_kst()
    connections: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    # 1) Official data APIs
    trade = None
    try:
        trade = comtrade_trade()
        connections["comtrade"] = {"status": "ok", "label": "UN Comtrade"}
    except Exception:
        connections["comtrade"] = {"status": "error", "label": "UN Comtrade"}
        errors.append("UN Comtrade")

    eia = None
    try:
        eia = eia_battery_capacity()
        connections["eia"] = {"status": "ok", "label": "U.S. EIA"}
    except Exception:
        connections["eia"] = {"status": "error", "label": "U.S. EIA"}
        errors.append("U.S. EIA")

    quotes: list[dict[str, Any]] = []
    try:
        quotes = alpha_quotes()
        connections["alpha"] = {"status": "ok", "label": "Alpha Vantage"}
    except Exception:
        connections["alpha"] = {"status": "error", "label": "Alpha Vantage"}
        errors.append("Alpha Vantage")

    eurostat = None
    try:
        eurostat = eurostat_ev_registrations()
        connections["eurostat"] = {"status": "ok", "label": "Eurostat"}
    except Exception:
        connections["eurostat"] = {"status": "error", "label": "Eurostat"}
        errors.append("Eurostat")

    world_bank = None
    try:
        world_bank = world_bank_manufacturing()
        connections["worldbank"] = {"status": "ok", "label": "World Bank"}
    except Exception:
        connections["worldbank"] = {"status": "error", "label": "World Bank"}
        errors.append("World Bank")

    # 2) GDELT news — free, no key
    queries = {
        "MATERIALS": '(battery OR lithium) (lithium OR nickel OR cobalt OR graphite OR cathode OR precursor) sourcelang:english',
        "POLICY": '("battery regulation" OR "battery policy" OR IRA OR FEOC OR "critical raw materials") sourcelang:english',
        "RECYCLING": '("battery recycling" OR "black mass" OR "recycled content" OR hydrometallurgy) sourcelang:english',
        "COMPANIES": '("LG Energy Solution" OR "Samsung SDI" OR "SK On" OR CATL OR BYD OR Panasonic) battery sourcelang:english',
        "TECHNOLOGY": '("solid state battery" OR LFP OR LMFP OR "sodium ion" OR "dry electrode" OR "silicon anode") sourcelang:english',
    }
    news: dict[str, list[dict[str, Any]]] = {}
    gdelt_ok = 0
    for key, query in queries.items():
        try:
            news[key] = gdelt_articles(query, timespan="7d", maxrecords=6)
            if news[key]:
                gdelt_ok += 1
        except Exception:
            news[key] = []
    connections["gdelt"] = {"status": "ok" if gdelt_ok else "error", "label": "GDELT"}
    if not gdelt_ok:
        errors.append("GDELT")

    # MARKET section
    market_lines: list[str] = []
    market_sources: list[dict[str, str]] = []
    if trade:
        exp = trade.get("exports")
        imp = trade.get("imports")
        if exp:
            change = trade.get("export_change")
            change_text = f" ({change:+.1f}% YoY)" if change is not None else ""
            market_lines.append(f"• 한국 리튬이온축전지(HS 850760) 수출 — {exp['year']}년 {fmt_money(exp['value'])}{change_text} [1]")
        if imp:
            change = trade.get("import_change")
            change_text = f" ({change:+.1f}% YoY)" if change is not None else ""
            market_lines.append(f"• 한국 리튬이온축전지(HS 850760) 수입 — {imp['year']}년 {fmt_money(imp['value'])}{change_text} [1]")
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
            f"• EU 신규 무배출 승용차 등록 — {fmt_number(eurostat['value'] / 1_000_000, 2)}백만 대 · {eurostat['year']}년 [{local_num}]"
        )
        market_sources.append(eurostat["source"])
    if world_bank and world_bank.get("rows"):
        local_num = len(market_sources) + 1
        wb_text = " · ".join(f"{r['country']} {r['value']:.1f}%({r['year']})" for r in world_bank["rows"])
        market_lines.append(f"• 주요 배터리 경제권 제조업 부가가치 비중 — {wb_text} [{local_num}]")
        market_sources.append(world_bank["source"])
    if not market_lines:
        market_lines.append("• 시장 데이터 연결 대기 — Render 환경변수의 무료 API 키 설정을 확인할 필요가 있음")
    market_lines.append("\n시사점: 이 화면의 수치는 공식 API에서 자동 갱신되며, 각 기관의 발표 시차 때문에 현재 날짜와 데이터 기준월·연도는 다를 수 있음")

    # News sections
    sections: dict[str, Any] = {
        "MARKET": {**SECTION_META["MARKET"], "text": "\n\n".join(market_lines), "sources": market_sources},
    }
    fallback_text = {
        "MATERIALS": "• 최근 7일 핵심광물·소재 뉴스 조회 결과가 없거나 GDELT 연결이 일시적으로 제한됨",
        "POLICY": "• 최근 7일 배터리 정책·규제 뉴스 조회 결과가 없거나 GDELT 연결이 일시적으로 제한됨",
        "RECYCLING": "• 최근 7일 배터리 재활용·재생원료 뉴스 조회 결과가 없거나 GDELT 연결이 일시적으로 제한됨",
        "TECHNOLOGY": "• 최근 7일 배터리 기술 뉴스 조회 결과가 없거나 GDELT 연결이 일시적으로 제한됨",
    }
    for key in ("MATERIALS", "POLICY", "RECYCLING", "TECHNOLOGY"):
        text, srcs = news_section(news.get(key, []), fallback_text[key])
        sections[key] = {**SECTION_META[key], "text": text, "sources": srcs}

    company_lines: list[str] = []
    company_sources: list[dict[str, str]] = []
    if quotes:
        for quote in quotes:
            local_num = len(company_sources) + 1
            change = quote.get("change_percent")
            change_text = f" · {change:+.2f}%" if change is not None else ""
            date_text = f" · {quote['latest_day']}" if quote.get("latest_day") else ""
            company_lines.append(f"• {quote['symbol']} — ${quote['price']:,.2f}{change_text}{date_text} [{local_num}]")
            company_sources.append(quote["source"])
    company_news = news.get("COMPANIES", [])[:3]
    for article in company_news:
        local_num = len(company_sources) + 1
        meta = " · ".join(x for x in [article.get("domain"), article.get("date")] if x)
        company_lines.append(f"• {article['title']} [{local_num}]" + (f"\n  {meta}" if meta else ""))
        company_sources.append(source(article["title"], article["url"]))
    if not company_lines:
        company_lines.append("• 기업 데이터 연결 대기 — Alpha Vantage 무료 API 키 또는 GDELT 연결상태 확인 필요")
    sections["COMPANIES"] = {**SECTION_META["COMPANIES"], "text": "\n\n".join(company_lines), "sources": company_sources}

    all_sources = assign_global_source_numbers(sections)

    # KPI cards
    kpis: list[dict[str, Any]] = []
    if trade and trade.get("exports"):
        exp = trade["exports"]
        kpis.append({
            "label": "한국 Li-ion 배터리 수출",
            "value": fmt_money(exp["value"]),
            "change": trade.get("export_change"),
            "meta": f"{exp['year']} · HS 850760",
            "source": "UN Comtrade",
        })
    if eia:
        kpis.append({
            "label": "미국 BESS 운영용량",
            "value": f"{fmt_number(eia['capacity_mw'] / 1000, 2)} GW",
            "change": None,
            "meta": f"{eia['period']} · utility-scale",
            "source": "U.S. EIA",
        })
    if eurostat:
        kpis.append({
            "label": "EU 무배출 승용차 신규등록",
            "value": f"{fmt_number(eurostat['value'] / 1_000_000, 2)}M",
            "change": None,
            "meta": f"{eurostat['year']} · Eurostat",
            "source": "Eurostat",
        })
    if quotes:
        for quote in quotes[:1]:
            kpis.append({
                "label": quote["symbol"],
                "value": f"${quote['price']:,.2f}",
                "change": quote.get("change_percent"),
                "meta": quote.get("latest_day") or "latest close",
                "source": "Alpha Vantage",
            })
    while len(kpis) < 4:
        kpis.append({"label": "무료 API", "value": "연결 대기", "change": None, "meta": "Render 환경변수 확인", "source": ""})

    ok_count = sum(1 for item in connections.values() if item["status"] == "ok")
    status = "live" if ok_count == len(connections) else ("partial" if ok_count else "offline")
    expires = generated.timestamp() + CACHE_TTL

    return {
        "status": status,
        "reason": "" if not errors else "연결 확인 필요: " + ", ".join(errors),
        "generated_at": generated.isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires, KST).isoformat(timespec="seconds"),
        "cache_seconds": CACHE_TTL,
        "sections": sections,
        "all_sources": all_sources,
        "kpis": kpis[:4],
        "connections": connections,
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
            # Never expose secrets or full request URLs in an error payload.
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
                "sections": {key: {**SECTION_META[key], "text": "데이터 연결 대기", "sources": []} for key in SECTION_KEYS},
                "all_sources": [],
                "kpis": [],
                "connections": {},
            }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/dashboard")
async def dashboard_api() -> dict[str, Any]:
    return await get_dashboard()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "time": now_kst().isoformat(timespec="seconds")}
