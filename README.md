# Battery Trend Briefing — Free API Edition v3

유료 OpenAI API 없이 무료/공개 API 6개로 동작하는 FastAPI 배터리 산업 대시보드입니다.

## 데이터 소스

1. **UN Comtrade** — 한국 리튬이온축전지(HS 850760) 연간 수출입
2. **U.S. EIA** — 미국 utility-scale 운영 배터리 저장용량
3. **Alpha Vantage** — ALB, SQM, TSLA 무료 Global Quote
4. **GDELT DOC 2.0** — 핵심광물, 정책, 재활용, 기업, 기술 최신 뉴스
5. **Eurostat** — EU 무배출/배터리 전기 승용차 등록 동향
6. **World Bank** — 한국·중국·미국·EU 제조업 부가가치 비중

GDELT, Eurostat, World Bank는 API key가 필요하지 않습니다.

## Render Environment Variables

Render > Web Service > Environment에서 아래만 입력합니다.

- `COMTRADE_API_KEY` = UN Comtrade 무료 키
- `EIA_API_KEY` = EIA 무료 키
- `ALPHAVANTAGE_API_KEY` = Alpha Vantage 무료 키
- `CACHE_TTL_SECONDS` = `21600`
- `STOCK_SYMBOLS` = `ALB,SQM,TSLA`

`OPENAI_API_KEY`, `OPENAI_MODEL`은 사용하지 않습니다.

## Render 설정

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`
- Free plan 사용 가능

## 참고

무료 API는 제공기관별 발표 시차와 호출 한도가 있습니다. Alpha Vantage 무료 Global Quote는 최신 제공 종가 기준일 수 있습니다. Eurostat와 World Bank는 인증키 없이 공개 API를 호출합니다.


## 문제 확인 URL
배포 후 `/api/status`를 열면 API 키의 설정 여부(true/false)와 각 데이터 소스 연결 오류를 키 값 노출 없이 확인할 수 있습니다.
