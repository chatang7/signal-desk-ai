# SIGNAL DESK FREE — 0원 예약 수집 대시보드

기존 SIGNAL DESK v2를 확장한 **무료 공개 소스 자동 수집 + 정적 대시보드** 구성입니다. 개인 컴퓨터가 꺼져 있어도 GitHub Actions가 매일 자동 실행됩니다. AI 유료 API, 별도 서버 결제, 카드 등록은 필요하지 않습니다. **처음 한 번의 GitHub 저장소 생성·게시 설정은 필요**합니다.

## 무엇이 자동인가?

- GitHub Actions 예약 실행: **매일 09:17, 21:17 한국시간** (UTC 00:17, 12:17). GitHub 정책상 정확한 분에 실행되는 것을 보장하지 않으며, 비활성 저장소의 예약 실행이 중지될 수 있습니다.
- 무료 공개 데이터를 Python 표준 라이브러리로 수집하고 `data/news.json`에 누적 저장(최대 최근 90일, 500건).
- GitHub Pages에 대시보드와 데이터가 자동 게시됨. 브라우저를 꺼두어도 수집은 계속됩니다.
- 대시보드는 방문 시 게시된 JSON을 읽고 기존 검토 상태, 수동 메모, Discord 초안을 **그 브라우저의 로컬 저장소에서** 유지합니다.

### 데이터 소스 13개

- 공식 GitHub 릴리스 8개: OpenAI Codex, Gemini CLI, Claude Code, Transformers, vLLM, Ollama, ComfyUI, llama.cpp
- 공식 블로그 RSS/Atom 4개: OpenAI, Google AI, Kakao Tech, NAVER D2 (관련 AI 키워드로 필터)
- 연구: Hugging Face Daily Papers (논문 본문은 arXiv 원문 링크)

소스별 오류는 앱의 **자동 수집** 탭과 `data/news.json`에 남으며, 소스에 문제가 있어도 이전 기록은 보존됩니다. 공식 릴리스 발표의 신뢰도와 발표 내용의 독립 검증을 혼동하지 않습니다. 비용·성능·기술 차이는 확인되지 않으면 빈칸/미정입니다. 제목·초록은 외부 원문 메타데이터입니다.

## 최초 1회 설치

### A. 가장 간단한 방법 (연결된 ChatGPT GitHub 사용)

1. [GitHub에서 저장소 만들기](https://github.com/new)를 엽니다. 이름 `signal-desk-ai`, **Public**, 기본 브랜치 `main`으로 만듭니다. 개인 메모나 토큰은 넣지 마세요.
2. ChatGPT와 GitHub 앱에 해당 저장소 접근을 허용한 다음 `signal-desk-ai 저장소 만들었어. 이 ZIP에 있는 프로젝트를 올려줘`라고 요청할 수 있습니다. 접근 권한이 없으면 파일 업로드가 불가능할 수 있습니다.
3. 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions**를 선택합니다.
4. **Actions → Collect AI news and publish dashboard → Run workflow**를 한 번 누릅니다. 처음 배포가 성공하면 `https://<GitHub사용자명>.github.io/signal-desk-ai/`에 접속합니다.

### B. 직접 업로드 (PC에서 git을 사용할 경우)

ZIP을 풀어 폴더로 이동한 다음, GitHub에 `signal-desk-ai`라는 **공개** 빈 저장소를 먼저 생성합니다. 아래의 `<GITHUB_USERNAME>`을 자신의 로그인명으로 교체하세요.

```bash
git init -b main
git add .
git commit -m "Initial free Signal Desk"
git remote add origin https://github.com/<GITHUB_USERNAME>/signal-desk-ai.git
git push -u origin main
```

그 뒤 A의 3~4단계를 진행하세요. 공개 저장소는 소스 코드와 자동 수집된 뉴스도 누구나 볼 수 있습니다. GitHub Free에서 Pages는 **공개 저장소**에 제공됩니다.

## 사용법

- 뉴스는 **뉴스 보관함**에서 확인합니다. 상태 변경·중복 후보 보기·필터·Discord 초안 편집은 기존 v2와 동일합니다.
- **자동 수집**에 표시되는 'GitHub 서버 수집 상태'는 자동화 결과입니다. 버튼 **저장된 피드 확인**은 수집을 실행하는 버튼이 아니라 서버가 기록한 결과를 다시 읽는 버튼입니다.
- `공개 API 수동 보충` 버튼은 방문자의 브라우저에서 기존 v2 방식으로 직접 추가 수집합니다. 백그라운드 수집과 별도입니다.
- 예전 HTML에서 수정했던 기사/메모를 새 웹사이트로 옮기려면 이전 대시보드의 **운영 설정 → JSON 백업**, 새 사이트의 **가져오기** 기능을 사용하세요. 브라우저의 저장 주소(origin)가 바뀌면 자동 이전되지 않습니다.
- 무료·저비용 필터는 **확인된 가격 등급에만** 적용됩니다. 공식 데이터가 무료라고 확증하지 못하면 '미정'이므로 필터에 표시되지 않습니다.

## 반드시 알아둘 한계

| 항목 | 상태 |
|---|---|
| 서버 예약 수집 | 코드 구현, **GitHub 설정 및 실제 예약 실행 미검증** |
| 공개 뉴스 백업/중복 URL 처리 | 구현, 단위/브라우저 모의 테스트 통과 |
| 실시간 독립 뉴스 사실검증 | 미구현 |
| AI가 작성하는 심층 평가·새 기술과의 차이 | **유료 AI API 미사용**이라 자동 생성하지 않음 |
| ChatGPT 예약 브리핑을 자동 전송 | 미구현. 기존 작업 변경 없음 |
| Discord 자동 게시 | 미구현 (초안 작성·복사 가능) |
| 기기 간 검토 상태 동기화 | 미구현. 기록은 사용 기기에만 저장 |
| 한국·해외 모든 뉴스 포함 | 미구현. 위 공개 소스 13개만 수집 |
| 사설로 보호되는 사용자 대시보드 | 미구현. GitHub Pages 사이트와 피드는 공개 |

GitHub Actions 실행은 가끔 지연·실패할 수 있고, 오래 활동이 없는 공개 저장소에서 예약 워크플로가 자동으로 비활성화될 수 있습니다. Pages 사용과 스케줄 무료 제공 정책은 GitHub에서 언제든 바뀔 수 있으니 [GitHub Pages 문서](https://docs.github.com/en/pages/quickstart)와 [GitHub Actions 문서](https://docs.github.com/en/actions)를 확인하세요.

## 구성 파일

- `index.html`: 기존 인터랙티브 대시보드(v3 수정)
- `scripts/collect.py`: API 키 없이 동작하는 서버 수집기
- `.github/workflows/collect-and-deploy.yml`: 매일 예약 수집 및 GitHub Pages 배포
- `data/news.json`: 정적 공개 피드 데이터 (첫 실행 이전에는 빈 배열)
- `tests/test_collect.py`: 단위 테스트
- `tests/test_browser.py`: 브라우저 모의 테스트(로컬 Playwright 설치된 경우에만 실행)

## 코드 보안/프라이버시

- 개인 API 키·토큰·Discord 웹훅을 소스에 넣지 않습니다.
- 모든 GitHub Pages 방문자는 **공개 뉴스 데이터**를 읽을 수 있습니다.
- 웹 브라우저의 로컬 검토 상태·메모는 `data/news.json`에 포함되지 않으며 서버로 올라가지 않습니다.
- 그러나 공유 PC 또는 브라우저 정리 시 메모가 유실될 수 있으므로 정기 JSON 백업을 권장합니다.