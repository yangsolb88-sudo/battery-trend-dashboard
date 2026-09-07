# RESET Used Battery Circular Briefing — v12 no-when


사용후 배터리 재활용·재생원료 중심 브리핑 사이트입니다.

## v12 수정 사항

- Google News RSS를 단일 OR 검색이 아니라 키워드별 개별 검색으로 변경
- 국내 검색어를 `폐배터리`, `배터리 재활용`, `사용후 배터리` 중심으로 완화
- 정책·기업·재생원료 메뉴도 너무 좁은 검색어 대신 넓은 검색어 우선 적용
- 결과가 적을 경우 Google 뉴스·네이버 뉴스 직접검색 버튼 표시
- 기본 기사 조회 기간을 최근 6개월로 설정
- OpenAI API 미사용

## 필요 환경변수

- `COMTRADE_API_KEY`: UN Comtrade 무료 키
- `EIA_API_KEY`: U.S. EIA 무료 키
- `CACHE_TTL_SECONDS`: 21600 권장

GDELT, Google News RSS, World Bank는 별도 키가 필요 없습니다.


## v12 변경사항

- Google News 직접검색 및 RSS 쿼리에서 `when:6m`, `when:7d`, `when:30d` 제거
- 최근 7일/최근 6개월 기간 제한은 RSS 기사 날짜(pubDate)를 서버에서 후처리
- 한국어 뉴스 검색 결과가 비는 문제 완화
- Render 환경변수는 기존 COMTRADE_API_KEY, EIA_API_KEY 그대로 사용
