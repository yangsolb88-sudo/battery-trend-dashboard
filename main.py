import asyncio
import html
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "21600"))  # 6 hours
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-astra")

app = FastAPI(title="Battery Trend Briefing", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_refresh_lock = asyncio.Lock()

SECTION_KEYS = ["MARKET", "MATERIALS", "POLICY", "RECYCLING", "COMPANIES", "TECHNOLOGY"]
SECTION_META = {
    "MARKET": {"title": "시장·수요", "subtitle": "EV·ESS·배터리 시장과 공급망"},
    "MATERIALS": {"title": "핵심광물·소재", "subtitle": "Li·Ni·Co·Mn·흑연·전구체"},
    "POLICY": {"title": "정책·규제", "subtitle": "한국·EU·미국·중국 규제"},
    "RECYCLING": {"title": "재활용·재생원료", "subtitle": "폐배터리·블랙매스·회수·재생원료"},
    "COMPANIES": {"title": "기업 동향", "subtitle": "국내외 셀·소재·재활용 기업"},
    "TECHNOLOGY": {"title": "기술 동향", "subtitle": "전고체·LFP·LMFP·Na-ion 등"},
}


def now_kst() -> datetime:
    return datetime.now(KST)


def demo_payload(reason: str = "OPENAI_API_KEY 미설정") -> dict[str, Any]:
    stamp = now_kst().isoformat(timespec="seconds")
    demo = {
        "MARKET": "• 실시간 데이터 연결 대기 — Render 환경변수에 OPENAI_API_KEY를 설정하면 최신 배터리 시장 동향을 웹검색해 표시함\n\n시사점: 현재 화면은 배포 전 확인을 위한 데모 모드임",
        "MATERIALS": "• 핵심광물·소재 데이터 연결 대기 — 리튬·니켈·코발트·흑연·전구체 가격 및 공급망 이슈를 최신 웹자료로 요약함",
        "POLICY": "• 정책·규제 데이터 연결 대기 — EU Battery Regulation, 미국 IRA·FEOC, 한국 자원순환 정책 등의 최신 변경을 추적함",
        "RECYCLING": "• 재활용·재생원료 데이터 연결 대기 — 블랙매스, 재생원료, 회수율, 재활용 투자 및 인증제도 이슈를 추적함",
        "COMPANIES": "• 기업 동향 데이터 연결 대기 — LG에너지솔루션, 삼성SDI, SK On, CATL, BYD 및 소재·재활용 기업 뉴스를 추적함",
        "TECHNOLOGY": "• 기술 동향 데이터 연결 대기 — 전고체, LFP/LMFP, 실리콘 음극, Na-ion 등 주요 기술 변화를 추적함",
    }
    return {
        "status": "demo",
        "reason": reason,
        "generated_at": stamp,
        "expires_at": stamp,
        "model": None,
        "sections": {
            key: {**SECTION_META[key], "text": demo[key], "sources": []}
            for key in SECTION_KEYS
        },
        "all_sources": [],
    }


def build_prompt() -> str:
    today = now_kst().strftime("%Y-%m-%d")
    return f"""
오늘은 한국시간 {today}이다.
당신은 배터리 산업 동향 모니터링 애널리스트다. 웹검색을 반드시 사용해서 최근 배터리 산업의 중요한 변화만 한국어로 정리하라.

목적:
- 국내 연구용역 및 배터리 정책·재활용 업무 담당자가 빠르게 최신 이슈를 파악할 수 있는 웹 대시보드용 브리핑
- 발표일과 실제 사건일을 구분하고 가능한 경우 정확한 날짜를 적을 것
- 단순 홍보성 기사는 제외하고 공식기관, 정부, 기업 IR/보도자료, Reuters·Bloomberg급 주요 매체, 전문기관 및 학술자료를 우선할 것
- 확인되지 않은 전망을 사실처럼 쓰지 말 것
- 각 항목은 2~3문장 이내로 압축할 것
- 중요 업데이트가 없으면 억지로 만들지 말고 '최근 유의미한 신규 업데이트가 제한적임'이라고 명시할 것

반드시 아래 6개 섹션 헤더를 정확히 그대로 사용하라. 다른 헤더는 만들지 말 것.
각 섹션은 핵심 항목 3개 이내와 마지막 '시사점:' 1개로 구성하라.
웹검색 출처에 대한 인라인 인용을 유지하라.

[MARKET]
EV·ESS·배터리 수요, 출하, 생산능력, 공급망, 시장 지표의 최근 30일 핵심 변화

[MATERIALS]
리튬·니켈·코발트·망간·흑연·양극재·전구체·전해질·동박 등 핵심광물/소재의 최근 30일 핵심 변화

[POLICY]
EU Battery Regulation 및 하위법령, 미국 배터리/IRA/FEOC 정책, 중국 규제, 한국 배터리·자원순환 정책의 최근 60일 핵심 변화

[RECYCLING]
폐배터리, 블랙매스, 재생원료, 재활용 효율·원료 회수, 배터리 여권, 재생원료 함량, 재활용 기업 투자·공장 가동의 최근 60일 핵심 변화

[COMPANIES]
LG에너지솔루션·삼성SDI·SK On·CATL·BYD·Panasonic 및 주요 소재·재활용 기업의 최근 14일 핵심 변화

[TECHNOLOGY]
전고체·LFP·LMFP·Na-ion·실리콘 음극·건식전극·고망간 등 상용화와 R&D의 최근 30일 핵심 변화

출력 예시 형식:
[MARKET]
• 제목 — 요약 ...
• 제목 — 요약 ...
시사점: ...

[MATERIALS]
...
""".strip()


def normalize_annotation(ann: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(ann, dict):
        return None
    data = ann.get("url_citation") if isinstance(ann.get("url_citation"), dict) else ann
    url = data.get("url")
    if not url:
        return None
    return {
        "url": url,
        "title": data.get("title") or url,
        "start_index": data.get("start_index"),
        "end_index": data.get("end_index"),
    }


def add_citation_markers(text: str, annotations: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    sources: list[dict[str, str]] = []
    source_num: dict[str, int] = {}
    inserts: dict[int, list[int]] = {}

    for raw in annotations:
        ann = normalize_annotation(raw)
        if not ann:
            continue
        url = ann["url"]
        if url not in source_num:
            source_num[url] = len(sources) + 1
            sources.append({"title": ann["title"], "url": url})
        num = source_num[url]
        end = ann.get("end_index")
        if isinstance(end, int) and 0 <= end <= len(text):
            inserts.setdefault(end, []).append(num)

    marked = text
    for pos in sorted(inserts.keys(), reverse=True):
        nums = sorted(set(inserts[pos]))
        marker = "".join(f"[{n}]" for n in nums)
        marked = marked[:pos] + marker + marked[pos:]

    return marked, sources


def extract_responses_data(response: Any) -> tuple[str, list[dict[str, Any]]]:
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    texts: list[str] = []
    anns: list[dict[str, Any]] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                texts.append(content.get("text", ""))
                anns.extend(content.get("annotations", []) or [])
    return "\n".join(texts).strip(), anns


def extract_chat_data(completion: Any) -> tuple[str, list[dict[str, Any]]]:
    payload = completion.model_dump() if hasattr(completion, "model_dump") else completion
    message = payload["choices"][0]["message"]
    return (message.get("content") or "").strip(), message.get("annotations", []) or []


def call_openai_search(prompt: str) -> tuple[str, list[dict[str, Any]], str]:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Preferred current Responses API path.
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            tools=[{"type": "web_search", "search_context_size": "low"}],
            input=prompt,
        )
        text, anns = extract_responses_data(response)
        if text:
            return text, anns, OPENAI_MODEL
    except Exception as first_error:
        # Compatibility fallback for accounts/model access where the Responses model is unavailable.
        try:
            completion = client.chat.completions.create(
                model="gpt-5-search-api",
                web_search_options={},
                messages=[{"role": "user", "content": prompt}],
            )
            text, anns = extract_chat_data(completion)
            if text:
                return text, anns, "gpt-5-search-api"
        except Exception as second_error:
            raise RuntimeError(f"OpenAI web search failed: {second_error}") from first_error

    raise RuntimeError("OpenAI web search returned an empty response")


def split_sections(marked_text: str, sources: list[dict[str, str]]) -> dict[str, Any]:
    pattern = r"\[(MARKET|MATERIALS|POLICY|RECYCLING|COMPANIES|TECHNOLOGY)\]"
    matches = list(re.finditer(pattern, marked_text))
    sections: dict[str, Any] = {}

    for i, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(marked_text)
        body = marked_text[start:end].strip()

        cited_nums = sorted({int(n) for n in re.findall(r"\[(\d+)\]", body)})
        section_sources = []
        for n in cited_nums:
            if 1 <= n <= len(sources):
                section_sources.append({"number": n, **sources[n - 1]})

        sections[key] = {
            **SECTION_META[key],
            "text": body or "최근 브리핑을 불러오지 못함",
            "sources": section_sources,
        }

    for key in SECTION_KEYS:
        sections.setdefault(
            key,
            {**SECTION_META[key], "text": "해당 섹션을 파싱하지 못함", "sources": []},
        )
    return sections


def generate_dashboard_sync() -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        return demo_payload()

    prompt = build_prompt()
    text, annotations, model_used = call_openai_search(prompt)
    marked_text, sources = add_citation_markers(text, annotations)
    sections = split_sections(marked_text, sources)
    generated = now_kst()
    expires = generated.timestamp() + CACHE_TTL

    return {
        "status": "live",
        "generated_at": generated.isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires, KST).isoformat(timespec="seconds"),
        "model": model_used,
        "sections": sections,
        "all_sources": [{"number": i + 1, **src} for i, src in enumerate(sources)],
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
        except Exception as exc:
            if _cache["data"] is not None:
                stale = dict(_cache["data"])
                stale["status"] = "stale"
                stale["reason"] = str(exc)
                return stale
            return demo_payload(reason=str(exc))


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/dashboard")
async def dashboard_api() -> dict[str, Any]:
    return await get_dashboard()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "time": now_kst().isoformat(timespec="seconds")}
