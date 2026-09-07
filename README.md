# RESET Used Battery Circular Briefing — v9 Solar-style

태양광 패널 브리핑 사이트와 유사한 **브리핑형 단일 페이지** 구성으로 만든 사용후 배터리 재활용·재생원료 동향 모니터입니다.

## 목적

- 사용후 배터리 재활용
- 배터리 재생원료
- 배터리 순환경제
- 배터리 재생원료 인증
- 배터리 여권

위 키워드를 중심으로 국내/해외 기사와 정책·기업·기술·시장 동향을 빠르게 확인합니다.

## 데이터 소스

- GDELT DOC 2.0: 국내/해외 기사
- UN Comtrade: 한국 리튬이온축전지 HS 850760 수출입
- U.S. EIA: 미국 운영 BESS 저장용량
- World Bank: 제조업 부가가치 보조지표

## Render 환경변수

필수

- `COMTRADE_API_KEY`
- `EIA_API_KEY`

선택

- `CACHE_TTL_SECONDS=21600`
- `NEWS_LOOKBACK_MONTHS=6`
- `FAST_ARTICLE_LIMIT=40`
- `DASHBOARD_PREVIEW_LIMIT=6`
- `HTTP_TIMEOUT_SECONDS=10`

## 배포 방법

기존 GitHub 저장소에 전체 파일을 덮어쓰기 업로드 후 `Commit changes`를 누르면 Render가 자동 배포합니다.

## 확인 URL

- `/` : 실제 웹사이트
- `/api/status` : API 연결상태
- `/api/dashboard` : 대시보드 데이터 원본
- `/api/articles?category=briefing&scope=all&period=week` : 기사 데이터
