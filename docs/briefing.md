# SIGNAL DESK 2단계: 자동 브리핑

## 현재 제공되는 기능

- 기존 GitHub Actions 수집 직후 `scripts/briefing.py` 실행. `data/briefing.json`에 최대 8건을 자동 선별하여 게시합니다.
- **기본 동작(추가 설정 0, 0원):** 공개 뉴스 원문에서 확인되는 내용만 뽑고, 중요도·활용 시 점검 항목·비용·성능 미확인 여부를 표시합니다. 이것은 **LLM의 심층 추론이나 독립 사실검증이 아닌 근거 기반 자동 정리**입니다.
- **선택 기능(무료 API 키 필요):** Google Gemini API의 무료 사용 가능 모델에 한해 한국어 분석을 보조 생성합니다. 모델에서 원문에 존재하는 증거 발췌를 받지 못하면 사용하지 않으며, 비용/성능에 원문에 없는 숫자가 포함되면 거부합니다.
- 대시보드의 `심층 브리핑` 메뉴 또는 `/briefing.html`에서 결과 확인, 한국/해외·AI 해설·읽지 않은 항목 필터, Discord용 초안 복사가 가능합니다.

## Gemini 무료 API를 사용하는 방법 (선택)

1. [Google AI Studio](https://aistudio.google.com/apikey)에서 무료 API 키를 만듭니다. Google이 정한 무료 한도와 개인정보·데이터 이용 정책을 먼저 확인합니다.
2. GitHub 저장소 `Settings → Secrets and variables → Actions → New repository secret`에서 이름 **`GEMINI_API_KEY`**로 등록합니다. 키는 **채팅, 파일, 코드, Pages에 붙여넣지 않습니다.**
3. GitHub Actions의 `Collect AI news (free)`를 수동 실행하거나 다음 예약 실행을 기다립니다.
4. API 무료 사용량/계정 제한을 넘으면 유료 API로 전환하지 않고 근거 기반 정리로 대체합니다. 현재 기본 설정은 `gemini-2.5-flash-lite`, 회차당 신규 항목 최대 4건만 호출합니다. 이미 생성된 분석은 동일 뉴스 내용에 한해 재사용합니다.

## 제한과 안전

- **무료 사용량은 보장되지 않습니다.** Google API 모델 지원, 호출 제한, 지역/계정 정책은 바뀔 수 있습니다. 사용자는 결제 계정으로 전환하지 않는 것이 좋습니다.
- 무료 Gemini API에 보낸 **공개 뉴스 문구**는 Google 제품 개선에 사용될 수 있습니다. 개인 메모·Discord 토큰·개인 데이터는 보내지 않습니다.
- 자동 분류와 실제 성능 비교는 다릅니다. 추정된 '활용 검증 방법'은 특정 제품의 우수성이 입증됐다는 뜻이 아닙니다.
- 기본 모드에서는 영문 뉴스 원문이 그대로 표시될 수 있습니다. 한국어 AI 보조 기능이 필요하면 키를 등록해야 합니다.
- 검토 상태는 해당 브라우저에만 저장됩니다. Discord 자동 게시 및 다른 장치 동기화는 포함되지 않습니다.
- 모델 분석은 완전한 사실 검증을 보장하지 않습니다. 수치·정책·라이선스 등 중요 결정은 출처 원문으로 확인하세요.

## 개발 점검

`python -m unittest discover -s tests -p 'test_*.py' -q`

`python scripts/collect.py && python scripts/briefing.py`

정적 페이지: `https://chatang7.github.io/signal-desk-ai/briefing.html`