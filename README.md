# Battery Trend Briefing

OpenAI Web Search를 이용해 최신 배터리 산업 동향을 한국어로 자동 정리하는 FastAPI 웹 대시보드입니다.

## 포함 기능

- 시장·수요
- 핵심광물·소재
- 정책·규제
- 재활용·재생원료
- 기업 동향
- 기술 동향
- 웹검색 URL citation 원문 링크
- 기본 6시간 서버 캐시
- API 장애 시 마지막 캐시 또는 데모 화면 유지
- Render 배포 설정 포함

## 1. 중요: 기존 API 키 폐기

채팅이나 문서에 노출된 API 키는 다시 사용하지 않는 것을 권장합니다. OpenAI 대시보드에서 해당 키를 폐기한 뒤 새 Project API Key를 발급하세요.

**실제 키는 `.env`, GitHub, HTML, JavaScript에 넣지 마세요.**
Render의 Secret Environment Variable `OPENAI_API_KEY`에만 저장합니다.

## 2. 로컬 실행

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
set OPENAI_API_KEY=새로발급한키
uvicorn main:app --reload
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="새로발급한키"
uvicorn main:app --reload
```

브라우저에서 `http://127.0.0.1:8000` 접속합니다.

API 키가 없어도 데모 화면으로 정상 실행됩니다.

## 3. GitHub 업로드

저장소를 만든 뒤 이 폴더 전체를 업로드합니다. `.env` 파일과 실제 API 키는 업로드하지 않습니다.

## 4. Render 배포

이 프로젝트에는 `render.yaml`이 포함되어 있습니다.

Render에서 GitHub 저장소를 연결해 Web Service를 생성한 뒤 다음 Secret을 설정합니다.

- `OPENAI_API_KEY`: 새로 발급한 OpenAI API 키
- `OPENAI_MODEL`: 기본 `gpt-6-astra`
- `CACHE_TTL_SECONDS`: 기본 `21600` (6시간)

수동 설정 시:

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

배포 후 `https://<서비스명>.onrender.com` 형태의 주소가 생깁니다.

## 5. 비용 제어

웹페이지를 열 때마다 OpenAI를 호출하지 않습니다. 서버는 기본 6시간 동안 결과를 캐시합니다.

갱신주기를 바꾸려면 Render 환경변수 `CACHE_TTL_SECONDS` 수정:

- 1시간: `3600`
- 3시간: `10800`
- 6시간: `21600`
- 12시간: `43200`

공개 사이트에서는 너무 짧은 주기를 권장하지 않습니다.

## 6. 모델 호환성

기본은 `gpt-6-astra` + Responses API `web_search`입니다. 해당 모델 접근이 불가능한 계정에서는 코드가 자동으로 `gpt-5-search-api` 기반 Chat Completions 검색을 한 번 시도합니다.

## 주요 파일

```text
battery-trend-dashboard/
├─ main.py
├─ requirements.txt
├─ render.yaml
├─ .env.example
├─ .python-version
├─ templates/
│  └─ index.html
└─ static/
   ├─ styles.css
   └─ app.js
```

## 보안 메모

OpenAI API 키를 `index.html` 또는 `app.js`에 직접 작성하면 브라우저 개발자도구에서 노출됩니다. 이 프로젝트는 브라우저가 FastAPI 서버만 호출하고, FastAPI 서버만 OpenAI API를 호출하도록 구성되어 있습니다.
