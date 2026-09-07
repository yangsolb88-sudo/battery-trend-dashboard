# RESET Used Battery Circular Briefing — v10 Solar-style RSS

사용후 배터리 재활용·재생원료 중심 브리핑 사이트입니다.

## v10 수정사항

- 태양광 브리핑형 화면에 가깝게 재구성
- 첫 화면 기사 조회를 GDELT 단독이 아니라 Google News RSS 우선 방식으로 변경
- 최근 7일은 빠르게 표시
- 최근 6개월은 버튼 조회
- AirPods, 휴대폰, 자동차 배터리 방전·교체 등 무관 기사 필터링
- 국내 뉴스 / 해외 뉴스 분리
- OpenAI API 사용하지 않음

## Render Environment

필수는 아래 2개입니다.

```txt
COMTRADE_API_KEY=본인 UN Comtrade 키
EIA_API_KEY=본인 EIA 키
CACHE_TTL_SECONDS=21600
```

Google News RSS, GDELT, World Bank는 별도 키가 필요 없습니다.
