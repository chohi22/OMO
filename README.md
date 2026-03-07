# Oh My OpenCode (OmO) 사용 가이드

> **"Install. Type `ultrawork`. Done."**
>
> Oh My OpenCode는 OpenCode 위에 올라가는 멀티 모델 에이전트 오케스트레이션 하네스입니다.
> 단일 AI 에이전트를 실제로 코드를 배포하는 조율된 개발 팀으로 변환합니다.

---

## 목차

1. [Oh My OpenCode란?](#1-oh-my-opencode란)
2. [설치하기](#2-설치하기)
3. [핵심 개념 이해하기](#3-핵심-개념-이해하기)
4. [에이전트 시스템](#4-에이전트-시스템)
5. [작업 모드](#5-작업-모드)
6. [슬래시 명령어](#6-슬래시-명령어)
7. [스킬 시스템](#7-스킬-시스템)
8. [카테고리 시스템](#8-카테고리-시스템)
9. [MCP 통합](#9-mcp-통합)
10. [설정 가이드](#10-설정-가이드)
11. [실전 활용 팁](#11-실전-활용-팁)
12. [문제 해결](#12-문제-해결)

---

## 1. Oh My OpenCode란?

Oh My OpenCode(OmO)는 OpenCode를 위한 플러그인으로, 여러 AI 모델을 역할에 맞게 자동으로 배치하는 **멀티 에이전트 오케스트레이션 시스템**입니다.

### 핵심 가치

| 특징 | 설명 |
|------|------|
| **멀티 모델 오케스트레이션** | Claude, GPT, Gemini, Kimi 등 여러 모델을 작업 유형에 따라 자동 배치 |
| **병렬 에이전트** | 5개 이상의 전문 에이전트를 동시에 실행. 진짜 개발 팀처럼 동작 |
| **Hash-Anchored 편집** | `LINE#ID` 콘텐츠 해시로 모든 편집을 검증. 잘못된 편집 원천 차단 |
| **Intent Gate** | 사용자의 진짜 의도를 먼저 분류한 후 행동. 오해 없는 작업 수행 |
| **LSP + AST 도구** | IDE 수준의 리팩토링, 진단, AST 기반 코드 검색/변환 |
| **Claude Code 호환** | 기존 hooks, commands, skills, MCPs, plugins 그대로 사용 가능 |

### OpenCode와의 관계

```
OpenCode = 기반 AI 코딩 에이전트 (Debian/Arch 같은 존재)
Oh My OpenCode = 기능이 풍부한 오케스트레이션 레이어 (Ubuntu/Omarchy 같은 존재)
```

---

## 2. 설치하기

### 가장 쉬운 방법 (권장)

LLM 에이전트 세션(Claude Code, AmpCode, Cursor 등)에 아래를 붙여넣기:

```
Install and configure oh-my-opencode by following the instructions here:
https://raw.githubusercontent.com/code-yeongyu/oh-my-opencode/refs/heads/dev/docs/guide/installation.md
```

> 에이전트가 알아서 설치합니다. 사람이 직접 하면 설정 오류가 생기기 쉽습니다.

### 수동 설치

```bash
bunx oh-my-opencode install   # 권장
npx oh-my-opencode install    # 대안
```

> 설치 후 별도 런타임(Bun/Node.js) 없이 독립 실행 가능합니다.

### 설치 확인

```bash
opencode --version
# 플러그인이 로드되었는지 확인
```

---

## 3. 핵심 개념 이해하기

### 3.1 Intent Gate (의도 분류)

모든 요청은 실행 전에 **의도 분류**를 거칩니다:

| 사용자 표현 | 분류되는 의도 | 에이전트 동작 |
|------------|-------------|-------------|
| "X 설명해줘", "Y가 어떻게 동작해?" | 조사/이해 | explore/librarian으로 조사 후 답변 |
| "X 구현해줘", "Y 추가해줘" | 구현 (명시적) | 계획 수립 후 위임 또는 직접 실행 |
| "X 확인해봐", "Y 조사해봐" | 조사 | explore로 탐색 후 결과 보고 |
| "X 에러가 나요", "Y가 깨졌어" | 수정 필요 | 진단 후 최소한으로 수정 |
| "리팩토링해줘", "개선해줘" | 개방형 변경 | 코드베이스 평가 후 접근법 제안 |

### 3.2 Hash-Anchored 편집

에이전트가 읽은 모든 라인에 콘텐츠 해시가 태깅됩니다:

```
11#VK| function hello() {
22#XJ|   return "world";
33#MB| }
```

- 파일이 변경되면 해시가 불일치하여 편집이 **자동 거부**됨
- 공백 재현 오류, 잘못된 라인 편집을 원천 차단
- Grok Code Fast 1 기준: 성공률 6.7% → **68.3%** 향상

### 3.3 병렬 실행

Oh My OpenCode는 기본적으로 **모든 것을 병렬**로 처리합니다:

```
사용자 요청: "인증 기능 추가해줘"
    ↓
[Sisyphus] 병렬 탐색 시작
    ├─ [Explore 1] auth 미들웨어 패턴 검색
    ├─ [Explore 2] 에러 핸들링 패턴 검색
    ├─ [Librarian 1] JWT 보안 베스트 프랙티스 검색
    └─ [Librarian 2] Express auth 패턴 검색
    ↓
결과 수집 후 구현 시작
```

---

## 4. 에이전트 시스템

Oh My OpenCode의 핵심은 **역할 전문화된 에이전트**입니다.

### 4.1 주요 에이전트

#### Sisyphus (시시포스) - 메인 오케스트레이터

> "시시포스는 매일 바위를 밀어올린다. 멈추지 않는다. 포기하지 않는다."

- **역할**: 계획 수립, 전문가 에이전트 위임, 작업 완료까지 추적
- **권장 모델**: Claude Opus 4.6, Kimi K2.5, GLM 5
- **특징**: 공격적 병렬 실행, 절대 중간에 멈추지 않음

#### Hephaestus (헤파이스토스) - 자율 워커

> "The Legitimate Craftsman" - 필요에 의해 태어난 장인

- **역할**: 목표만 주면 알아서 코드베이스 탐색, 패턴 연구, 구현
- **모델**: GPT-5.3 Codex
- **특징**: 지시가 아닌 목표를 줘야 함. 자율적으로 끝까지 수행

#### Prometheus (프로메테우스) - 전략 기획자

- **역할**: 실제 엔지니어처럼 인터뷰하고, 범위와 모호성을 파악하여 상세 계획 수립
- **활성화**: `Tab`키로 Prometheus 모드 진입 또는 `@plan "작업"`
- **권장 모델**: Claude Opus 4.6, Kimi K2.5, GLM 5

#### Oracle (오라클) - 읽기 전용 컨설턴트

- **역할**: 아키텍처 결정, 복잡한 디버깅, 보안/성능 자문
- **특징**: 코드를 직접 수정하지 않음. 자문만 제공
- **사용 시점**: 2번 이상 수정 실패 시, 복잡한 아키텍처 결정 시

### 4.2 지원 에이전트

| 에이전트 | 역할 | 비용 | 사용 시점 |
|---------|------|------|----------|
| **Explore** | 코드베이스 빠른 검색 (Contextual Grep) | 저렴 | 파일 구조, 패턴 발견 시 |
| **Librarian** | 외부 문서/OSS 코드 검색 (Reference Grep) | 저렴 | 라이브러리 사용법, 공식 문서 필요 시 |
| **Metis** | 계획 분석, 빈틈 파악 | 비쌈 | 복잡한 요청의 범위 명확화 시 |
| **Momus** | 계획 리뷰, 검증 | 비쌈 | 작업 계획의 품질 검증 시 |
| **Atlas** | Prometheus 계획 실행, 작업 분배 | 보통 | `/start-work` 명령 시 |

### 4.3 에이전트 위임 흐름

```
사용자 요청
    ↓
[Intent Gate] — 진짜 의도 분류
    ↓
[Sisyphus] — 계획 수립 및 위임
    ↓
    ├── [Prometheus] → 전략적 계획 (인터뷰 모드)
    ├── [Atlas] → Todo 오케스트레이션 및 실행
    ├── [Oracle] → 아키텍처 자문
    ├── [Librarian] → 문서/코드 검색
    ├── [Explore] → 코드베이스 빠른 검색
    └── [Category 기반 에이전트] → 작업 유형별 전문 에이전트
```

---

## 5. 작업 모드

### 5.1 Ultrawork 모드 - "그냥 해줘"

```
ultrawork
```

또는 줄여서:

```
ulw
```

- 에이전트가 **모든 것을 알아서** 처리
- 코드베이스 탐색 → 패턴 연구 → 구현 → 진단 검증
- **완료될 때까지** 계속 작업
- 최소 구독만으로 작동:
  - ChatGPT ($20/월)
  - Kimi Code ($0.99/월)
  - GLM Coding Plan ($10/월)

### 5.2 Prometheus 모드 - "꼼꼼하게"

1. `Tab` 키로 Prometheus 모드 진입
2. Prometheus가 실제 엔지니어처럼 **인터뷰** 진행
3. 범위, 모호성 파악 후 상세 계획 수립
4. `/start-work`로 Atlas가 실행 시작

**이런 경우에 사용:**
- 여러 날에 걸친 대규모 프로젝트
- 프로덕션 중요 변경
- 복잡한 리팩토링
- 결정 기록이 필요할 때

### 5.3 Ralph Loop - "끝날 때까지"

자기 참조 루프. 작업이 **100% 완료**될 때까지 멈추지 않습니다.

```
/ralph-loop
```

### 5.4 ULW Loop

Ultrawork 모드로 완료까지 계속 진행:

```
/ulw-loop
```

---

## 6. 슬래시 명령어

| 명령어 | 설명 | 사용 시점 |
|--------|------|----------|
| `/ulw-loop` | Ultrawork 루프 시작 - 완료까지 계속 | 대부분의 작업에 권장 |
| `/ralph-loop` | 자기 참조 개발 루프 시작 | 복잡한 반복 작업 |
| `/cancel-ralph` | 활성 Ralph Loop 취소 | 루프 중단 필요 시 |
| `/init-deep` | 계층적 AGENTS.md 지식 베이스 초기화 | 새 프로젝트 시작 시 (강력 권장) |
| `/start-work` | Prometheus 계획 기반 작업 세션 시작 | 기획 완료 후 실행 시 |
| `/refactor` | LSP, AST-grep 기반 지능형 리팩토링 | 대규모 리팩토링 시 |
| `/handoff` | 새 세션에서 작업 계속을 위한 컨텍스트 요약 생성 | 세션 전환 시 |
| `/stop-continuation` | 모든 연속 메커니즘 중지 | 에이전트 멈춤 필요 시 |

### `/init-deep` 상세

프로젝트 전체에 계층적 `AGENTS.md` 파일을 자동 생성합니다:

```
project/
├── AGENTS.md              ← 프로젝트 전체 컨텍스트
├── src/
│   ├── AGENTS.md          ← src 고유 컨텍스트
│   └── components/
│       └── AGENTS.md      ← 컴포넌트 고유 컨텍스트
```

- 에이전트가 관련 컨텍스트를 **자동으로** 읽음
- 수동 관리 불필요
- 토큰 효율성과 에이전트 성능 모두 개선

---

## 7. 스킬 시스템

스킬은 단순한 프롬프트가 아닙니다. 각 스킬은 다음을 포함합니다:

- **도메인 특화 시스템 지침**
- **내장 MCP 서버** (온디맨드)
- **범위 지정된 권한** (에이전트가 벗어나지 않음)

### 7.1 내장 스킬

| 스킬 | 설명 | 트리거 |
|------|------|--------|
| `playwright` | 브라우저 자동화 (검증, 스크래핑, 테스트, 스크린샷) | 브라우저 관련 모든 작업 |
| `git-master` | Git 작업 전문 (atomic commit, rebase, blame, bisect) | 'commit', 'rebase', 'squash' 등 |
| `frontend-ui-ux` | 디자인 시안 없이도 멋진 UI/UX 구현 | 프론트엔드 디자인 작업 |
| `dev-browser` | 지속적 페이지 상태 기반 브라우저 자동화 | 'go to [url]', 'click on' 등 |

### 7.2 커스텀 스킬 추가

스킬 디렉토리에 `SKILL.md` 파일을 생성합니다:

```
# 프로젝트별 스킬
.opencode/skills/my-skill/SKILL.md

# 사용자 전역 스킬
~/.config/opencode/skills/my-skill/SKILL.md
```

### 7.3 Skill-Embedded MCP

스킬은 자체 MCP 서버를 가져옵니다:

- 작업 시에만 활성화 (온디맨드)
- 작업 범위에 한정
- 완료 후 자동 해제
- **컨텍스트 윈도우를 깨끗하게 유지**

---

## 8. 카테고리 시스템

에이전트가 작업을 위임할 때 **모델 이름**이 아닌 **카테고리**를 선택합니다.
카테고리가 자동으로 최적의 모델에 매핑됩니다.

### 8.1 기본 카테고리

| 카테고리 | 용도 | 기본 모델 예시 |
|---------|------|---------------|
| `visual-engineering` | 프론트엔드, UI/UX, 디자인, 스타일링 | Gemini 3.1 Pro |
| `ultrabrain` | 어려운 로직, 아키텍처 결정 | GPT-5.3 Codex |
| `deep` | 자율적 문제 해결, 깊은 연구 필요 | GPT-5.3 Codex |
| `quick` | 단일 파일 변경, 오타 수정 | Claude Haiku 4.5 |
| `artistry` | 창의적이고 비표준적인 접근 | 모델 다양 |
| `writing` | 문서, 기술 문서 작성 | 모델 다양 |
| `unspecified-low` | 기타 저노력 작업 | 경량 모델 |
| `unspecified-high` | 기타 고노력 작업 | 고성능 모델 |

### 8.2 카테고리 커스터마이징

```jsonc
// oh-my-opencode.json
{
  "categories": {
    "visual-engineering": {
      "model": "google/gemini-3.1-pro",
      "variant": "high"
    },
    "quick": {
      "model": "anthropic/claude-haiku-4-5"
    },
    "ultrabrain": {
      "model": "openai/gpt-5.3-codex",
      "variant": "xhigh"
    }
  }
}
```

---

## 9. MCP 통합

Oh My OpenCode에는 **빌트인 MCP 서버**가 포함되어 있습니다:

### 9.1 내장 MCP

| MCP | 기능 | 사용 시점 |
|-----|------|----------|
| **Exa (websearch)** | 웹 검색 및 실시간 정보 | 최신 정보, 라이브러리 문서 검색 |
| **Context7** | 공식 문서 및 코드 예제 조회 | 라이브러리 API 사용법 확인 |
| **Grep.app** | GitHub 코드 검색 | 실제 프로덕션 코드 패턴 검색 |

### 9.2 내장 도구

| 도구 | 기능 |
|------|------|
| `lsp_rename` | 워크스페이스 전체 심볼 이름 변경 |
| `lsp_goto_definition` | 정의로 이동 |
| `lsp_find_references` | 모든 참조 검색 |
| `lsp_diagnostics` | 빌드 전 에러/경고 확인 |
| `ast_grep_search` | 25개 언어 AST 기반 코드 패턴 검색 |
| `ast_grep_replace` | AST 인식 코드 패턴 치환 |

### 9.3 세션 도구

과거 작업 기록을 검색하고 활용할 수 있습니다:

| 도구 | 기능 |
|------|------|
| `session_list` | 모든 세션 목록 조회 |
| `session_read` | 세션 메시지 및 이력 읽기 |
| `session_search` | 세션 내용 전체 텍스트 검색 |
| `session_info` | 세션 메타데이터 및 통계 |

---

## 10. 설정 가이드

### 10.1 설정 파일 위치

| 위치 | 용도 | 우선순위 |
|------|------|---------|
| `.opencode/oh-my-opencode.jsonc` | 프로젝트 설정 | 높음 (프로젝트별) |
| `~/.config/opencode/oh-my-opencode.json` | 사용자 전역 설정 | 낮음 (기본) |

> JSONC(주석 포함 JSON)를 지원합니다.

### 10.2 주요 설정 예시

```jsonc
{
  // JSON Schema로 자동완성 지원
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-opencode/dev/assets/oh-my-opencode.schema.json",

  // 에이전트 모델 설정
  "agents": {
    "sisyphus": {
      "model": "anthropic/claude-opus-4-6",
      "variant": "high"
    },
    "oracle": {
      "model": "openai/gpt-5.4",
      "variant": "high"
    },
    "librarian": {
      "model": "google/gemini-3-flash"
    },
    "explore": {
      "model": "github-copilot/grok-code-fast-1"
    }
  },

  // 카테고리별 모델 설정
  "categories": {
    "visual-engineering": {
      "model": "google/gemini-3.1-pro",
      "variant": "high"
    },
    "quick": {
      "model": "anthropic/claude-haiku-4-5"
    }
  }
}
```

### 10.3 모델 선택 가이드

#### Claude 계열 (지시 따르기, 구조화된 출력 우수)
- **Claude Opus 4.6** — 최고 종합 경험. Sisyphus 최적
- **Claude Sonnet 4.6** — 성능과 비용의 균형
- **Claude Haiku 4.5** — 빠르고 저렴한 작업용

#### GPT 계열 (명시적 추론, 원칙 기반)
- **GPT-5.3 Codex** — 깊은 코딩 파워하우스. Hephaestus 전용
- **GPT-5.4** — 높은 지능. Oracle 기본
- **GPT-5 Nano** — 초저렴, 빠른 유틸리티 작업

#### 기타
- **Gemini 3 Pro** — 시각적/프론트엔드 작업 우수
- **Kimi K2.5** — Claude와 유사한 동작. 저렴한 대안
- **GLM 5** — Claude와 유사. 범용 작업 적합

---

## 11. 실전 활용 팁

### 11.1 처음 시작할 때

```
1. 프로젝트 루트에서 /init-deep 실행
   → 계층적 AGENTS.md가 생성되어 에이전트가 프로젝트를 이해합니다.

2. ultrawork (또는 ulw) 입력
   → 에이전트가 알아서 탐색, 연구, 구현, 검증합니다.
```

### 11.2 효과적인 요청 방법

**좋은 요청:**
```
"사용자 인증 기능을 JWT 기반으로 추가해줘. 
 기존 미들웨어 패턴을 따르고, 테스트도 작성해줘."
```

**보통 요청:**
```
"인증 추가해줘"
→ 에이전트가 Intent Gate로 의도를 파악하고 
   모호한 부분은 질문합니다.
```

**비효율적인 요청:**
```
"코드 좀 고쳐줘"
→ 어떤 코드? 무슨 문제? 에이전트가 질문해야 합니다.
```

### 11.3 복잡한 프로젝트 작업

1. **Prometheus 모드 사용**: `Tab`으로 진입
2. 에이전트의 인터뷰 질문에 답변
3. 계획 확인 후 `/start-work`
4. Atlas가 전문 에이전트에 작업 분배
5. 각 완료를 독립적으로 검증

### 11.4 백그라운드 에이전트 활용

Sisyphus는 자동으로 백그라운드 에이전트를 활용합니다:

- **Explore** — 코드베이스 패턴 검색 (내부)
- **Librarian** — 외부 문서/코드 검색 (외부)
- 둘 다 **저렴하고 빠름** → 자유롭게 병렬 실행
- 시스템이 완료 시 자동 알림

### 11.5 세션 관리

이전 작업 기록을 활용할 수 있습니다:

```
"어제 작업한 인증 구현 세션 찾아줘"
→ session_search로 검색
→ session_read로 상세 확인
→ 컨텍스트를 이어서 작업 가능
```

### 11.6 Git 작업

`git-master` 스킬이 자동 로드됩니다:

```
"변경사항 커밋해줘"
→ atomic commit 생성
→ 커밋 메시지 자동 생성
→ 커밋 전 hook 준수
```

### 11.7 프론트엔드 작업

```
"로그인 페이지 UI 만들어줘"
→ 카테고리: visual-engineering
→ frontend-ui-ux 스킬 자동 로드
→ 디자인 시안 없이도 시니어급 UI 구현
```

---

## 12. 문제 해결

### Q: 에이전트가 멈추거나 반복할 때

```
/stop-continuation
```

모든 연속 메커니즘(Ralph Loop, Todo Continuation 등)을 중지합니다.

### Q: 에이전트가 잘못된 방향으로 갈 때

- 에이전트에게 직접 "멈춰" 또는 "다른 접근법으로 해줘"라고 말하세요
- Sisyphus는 사용자의 우려를 인식하고 대안을 제안합니다

### Q: 모델을 변경하고 싶을 때

`~/.config/opencode/oh-my-opencode.json`에서 해당 에이전트의 `model` 필드를 수정:

```jsonc
{
  "agents": {
    "sisyphus": {
      "model": "kimi-for-coding/k2p5"  // Claude 대신 Kimi 사용
    }
  }
}
```

### Q: 커스텀 스킬을 추가하고 싶을 때

```
~/.config/opencode/skills/my-skill/SKILL.md
```

파일에 도메인 지침과 MCP 서버 설정을 작성합니다.

### Q: 플러그인을 제거하고 싶을 때

1. `~/.config/opencode/opencode.json`에서 `"oh-my-opencode"` 제거
2. 설정 파일 삭제 (선택):
   ```bash
   rm -f ~/.config/opencode/oh-my-opencode.json
   rm -f .opencode/oh-my-opencode.json
   ```

---

## 참고 링크

| 리소스 | URL |
|--------|-----|
| GitHub 저장소 | https://github.com/code-yeongyu/oh-my-opencode |
| 공식 홈페이지 | https://ohmyopencode.org |
| 설치 가이드 | [docs/guide/installation.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/guide/installation.md) |
| 오케스트레이션 가이드 | [docs/guide/orchestration.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/guide/orchestration.md) |
| 설정 레퍼런스 | [docs/reference/configuration.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/reference/configuration.md) |
| 기능 레퍼런스 | [docs/reference/features.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/reference/features.md) |
| 에이전트-모델 매칭 가이드 | [docs/guide/agent-model-matching.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/guide/agent-model-matching.md) |
| 매니페스토 | [docs/manifesto.md](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/manifesto.md) |
| Discord 커뮤니티 | https://discord.gg/PUwSMR9XNk |

---


>
> — Oh My OpenCode
