# 사용후 배터리 재활용 동향 브리핑 v5

무료 공개 API 기반의 국내·해외 사용후 배터리 재활용 뉴스 모니터링 대시보드입니다.

## 기본 뉴스 조사 조건

- 기본 조사기간: **최근 6개월**
- 데이터 갱신 캐시: **6시간** (`CACHE_TTL_SECONDS=21600`)
- 국내 뉴스: 한국 매체 중심
- 해외 뉴스: 한국 외 글로벌 매체
- 뉴스 API: GDELT DOC 2.0

GDELT ArticleList의 넓은 검색구간 처리 특성을 고려하여 최근 6개월을 약 3개월 단위 검색창으로 나누어 조회한 뒤 통합합니다.

## 추적 키워드

| No. | 한국어 | English | 日本語 | 中文（简体） |
|---|---|---|---|---|
| 1 | 사용후 배터리 재활용 | End-of-life battery recycling | 使用済み電池リサイクル | 退役电池回收利用 |
| 2 | 배터리 재생원료 | Recycled battery materials | 電池再生原料 | 电池再生原料 |
| 3 | 배터리 순환경제 | Battery circular economy | 電池循環経済 | 电池循环经济 |
| 4 | 배터리 재생원료 인증 | Recycled battery materials certification | 電池再生原料認証 | 电池再生原料认证 |
| 5 | 배터리 여권 | Battery passport | バッテリーパスポート | 电池护照 |

화면에는 4개 언어 키워드 사전을 표시하고, GDELT의 글로벌 뉴스 검색에는 해당 개념의 영문 검색식을 사용하여 한국·영어권뿐 아니라 GDELT가 수집하는 글로벌 기사까지 검색합니다.

## 사용 API

- GDELT: 국내·해외 뉴스
- UN Comtrade: 한국 HS 850760 수출입
- U.S. EIA: 미국 운영 BESS 용량
- Eurostat: EU battery-only 승용차 신규등록
- World Bank: 제조업 기반지표
- Alpha Vantage: 관련 상장사 보조 시장지표

## Render 환경변수

필수/권장 설정:

```text
COMTRADE_API_KEY=본인키
EIA_API_KEY=본인키
ALPHAVANTAGE_API_KEY=본인키
CACHE_TTL_SECONDS=21600
STOCK_SYMBOLS=ALB,SQM,TSLA
NEWS_LOOKBACK_MONTHS=6
```

GDELT, Eurostat, World Bank는 별도 API Key가 필요하지 않습니다.

## Render 실행

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Health Check Path:

```text
/health
```
