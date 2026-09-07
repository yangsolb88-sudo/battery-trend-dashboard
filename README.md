# Battery Trend Briefing — Free API Edition

유료 OpenAI API를 사용하지 않고 무료/공개 API만으로 동작하는 FastAPI 배터리 산업 대시보드입니다.

## 데이터 소스

- UN Comtrade: 한국 리튬이온축전지(HS 850760) 연간 수출입
- U.S. EIA: 미국 utility-scale 운영 배터리 저장용량
- Alpha Vantage: ALB, SQM, TSLA 무료 Global Quote (무료 계정은 실시간 체결가가 아니라 최신 제공 종가 기준일 수 있음)
- GDELT DOC 2.0: 핵심광물, 정책, 재활용, 기업, 기술 관련 최신 뉴스 (API key 불필요)

## Render Environment Variables

Render > 해당 Web Service > Environment에서 아래 값을 입력합니다.

- `COMTRADE_API_KEY` = UN Comtrade에서 발급받은 무료 키 (없어도 Preview API fallback 가능)
- `EIA_API_KEY` = EIA에서 발급받은 무료 키
- `ALPHAVANTAGE_API_KEY` = Alpha Vantage에서 발급받은 무료 키
- `CACHE_TTL_SECONDS` = `21600`
- `STOCK_SYMBOLS` = `ALB,SQM,TSLA`

`OPENAI_API_KEY`, `OPENAI_MODEL`은 이 버전에서는 사용하지 않습니다.

## Render 설정

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`
- Free plan 사용 가능

## 주의

무료 API는 호출한도와 데이터 발표 시차가 있습니다. Alpha Vantage 무료 Global Quote는 공식 문서상 기본 무료 사용에서 실시간 미국 주가가 아니라 최신 제공 종가 데이터를 반환할 수 있습니다.
