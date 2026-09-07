# 사용후 배터리 재활용 동향 브리핑 v6

국내·해외 **사용후 배터리 재활용** 동향을 모니터링하는 무료 공개 API 기반 FastAPI 대시보드입니다.

이번 v6는 일반 배터리 뉴스가 아니라 아래 5개 키워드를 기준으로 국내/해외 뉴스를 분리합니다.

| No. | 한국어 | English | 日本語 | 中文（简体） |
|---|---|---|---|---|
| 1 | 사용후 배터리 재활용 | End-of-life battery recycling | 使用済み電池リサイクル | 退役电池回收利用 |
| 2 | 배터리 재생원료 | Recycled battery materials | 電池再生原料 | 电池再生原料 |
| 3 | 배터리 순환경제 | Battery circular economy | 電池循環経済 | 电池循环经济 |
| 4 | 배터리 재생원료 인증 | Recycled battery materials certification | 電池再生原料認証 | 电池再生原料认证 |
| 5 | 배터리 여권 | Battery passport | バッテリーパスポート | 电池护照 |

## 주요 변경사항

- 국내 사용후 배터리 뉴스와 해외 사용후 배터리 뉴스를 별도 섹션으로 분리
- AirPods, 휴대폰 배터리 교체, 일반 차량 기사 등 불필요한 기사 필터링 강화
- 기본 뉴스 조사기간을 최근 6개월로 설정
- Alpha Vantage는 호출 제한 때문에 기본 비활성화
- Eurostat는 화면을 깨뜨리는 KPI가 아니라 보조 참고 링크로 처리
- 에러 배너는 핵심 뉴스 API 장애가 있을 때만 표시

## 사용 데이터 소스

- GDELT DOC 2.0: 국내·해외 뉴스
- UN Comtrade: 한국 리튬이온축전지 HS 850760 수출입
- U.S. EIA: 미국 utility-scale battery storage 운영용량
- World Bank: 제조업 부가가치 비중
- Eurostat: EU 전기차 등록 관련 보조 통계 링크
- Alpha Vantage: 선택사항. `ENABLE_ALPHA=true`일 때만 호출

## Render 환경변수

필수/권장:

```text
COMTRADE_API_KEY=본인 UN Comtrade 키
EIA_API_KEY=본인 EIA 키
CACHE_TTL_SECONDS=21600
NEWS_LOOKBACK_MONTHS=6
ENABLE_ALPHA=false
```

선택사항:

```text
ALPHAVANTAGE_API_KEY=본인 Alpha Vantage 키
STOCK_SYMBOLS=ALB,SQM,TSLA
```

Alpha Vantage는 무료 호출 제한이 자주 걸리므로 기본값은 `ENABLE_ALPHA=false`입니다.

## Render 실행 설정

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

## 확인 주소

배포 후 아래 주소를 확인합니다.

```text
/
/api/dashboard
/api/status
/health
```

`/api/status`에서 GDELT, UN Comtrade, U.S. EIA, World Bank 연결 상태를 확인할 수 있습니다.
