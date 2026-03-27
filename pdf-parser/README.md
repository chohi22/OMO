# PDF 문서 뷰어 및 파서

PDF 문서를 웹에서 조회·검색할 수 있는 Flask 기반 웹 애플리케이션입니다.
CJK(한글) 및 라틴 문자의 폰트 인코딩 중복 문제를 PyMuPDF 기반으로 근본적으로 해결합니다.

## 구성 파일

| 파일 | 설명 |
|------|------|
| `web_viewer.py` | Flask 웹 서버 (PDF 업로드, 페이지 조회, 전문 검색, 테이블 추출) |
| `pdf_parser.py` | CLI용 PDF 텍스트 추출기 |
| `generate_ppt.py` | PPT 슬라이드 자동 생성 유틸리티 |

## 주요 기능

- PDF 업로드 및 페이지별 텍스트 조회
- 전문 검색 (PostgreSQL 기반)
- 테이블 자동 추출 및 HTML 렌더링 (PyMuPDF `find_tables`)
- CJK 폰트 인코딩 버그 자동 보정 (블록 간 중복 문자 제거)
- OCR 폴백 (Tesseract 설치 시)
- 사용자 인증 (Flask-Login)
- 문서 재파싱 (`/reparse/<doc_id>`, `/reparse-all`)

## 요구 사항

- Python 3.12+
- PostgreSQL (DB명: `appdb`)
- 주요 패키지:
  - PyMuPDF (`fitz`)
  - Flask, Flask-Login
  - psycopg2
  - pdfplumber
  - Pillow
  - (선택) Tesseract OCR + pytesseract

## 설치 및 실행

```bash
# 패키지 설치
pip install PyMuPDF Flask flask-login psycopg2-binary pdfplumber Pillow

# (선택) OCR 지원
sudo apt install tesseract-ocr tesseract-ocr-kor
pip install pytesseract

# 웹 서버 실행
python web_viewer.py
```

서버가 `http://0.0.0.0:5000`에서 시작됩니다.

## CLI 텍스트 추출

```bash
python pdf_parser.py <pdf_path> [output_path]
```

## DB 설정

PostgreSQL 접속 정보는 `web_viewer.py` 내 `DB_CONFIG`에서 설정합니다.
테이블(`pdf_documents`, `pdf_pages`, `users`)은 서버 시작 시 자동 생성됩니다.

## PDF 파싱 방식

1. **텍스트 추출**: PyMuPDF `rawdict`에서 문자별 좌표를 수집하고, Y좌표 tolerance(3pt) 클러스터링 + X좌표 high-water mark로 블록 간 중복 문자를 제거합니다.
2. **테이블 추출**: PyMuPDF `find_tables()`를 사용하여 CID 폰트 ToUnicode 중복 버그 없이 정확하게 추출합니다.
3. **CJK 중복 감지**: 조사 중복(은은, 는는 등) 및 비율 기반 감지로, 필요 시 후처리 보정 또는 OCR 폴백을 적용합니다.

---

## 문제 해결 이력: CJK/라틴 문자 중복 버그

### 문제 현상

스위스 노동법 등 CID 폰트를 사용하는 PDF를 웹 뷰어에서 열면 한글과 독일어 텍스트가 글자마다 중복되어 표시되었습니다.

```
❌ 33 이 법은은 가능한 한 외국국기업 의의 스위스스 내 사업업장에서 근로 하하는 근로로자에게도도 적용하하도록 한한다.
❌ GGeltungsbbereich DDas Gesettz ist unnter Vorrbehalt
```

### 원인 분석

원인이 2가지였습니다.

#### 원인 1: 텍스트 추출 — Y좌표 그룹핑 오류 (`content` 컬럼)

PyMuPDF `rawdict`에서 문자별 좌표를 수집한 뒤, 같은 줄의 문자를 묶어 중복을 제거하는 방식이었는데, Y좌표를 `round(y, 1)` (소수점 1자리 반올림)로 그룹핑했습니다.

같은 줄의 문자라도 Y값이 `100.05` vs `100.15`이면 각각 `100.1`과 `100.2`로 반올림되어 **다른 줄로 분리**됩니다. 그러면 high-water mark가 리셋되어 중복 문자를 건너뛰지 못합니다.

#### 원인 2: 테이블 추출 — pdfplumber의 한계 (`table_html` 컬럼)

테이블은 `pdfplumber`로 추출했는데, pdfplumber는 내부적으로 pdfminer를 사용합니다. 이 PDF의 CID 폰트에 ToUnicode 테이블 버그가 있어서, pdfminer가 하나의 글리프를 **2개의 문자로 매핑**합니다. 이 경우 각 문자가 서로 다른 위치에 순차 배치되므로, 좌표 기반 중복 제거로는 해결할 수 없었습니다.

웹에서 보이던 중복은 **주로 이 `table_html`** 때문이었습니다.

### 해결 방법

#### 1. Y좌표 그룹핑 수정 (`web_viewer.py`, `pdf_parser.py`)

```python
# 기존: 반올림 경계에서 같은 줄이 분리됨
y_key = round(bbox[1], 1)

# 수정: tolerance 3pt 기반 클러스터링
Y_TOL = 3.0
if abs(y_top - current_y) > Y_TOL:
    # 새 줄 시작
```

같은 줄의 문자가 Y값이 약간 달라도 3pt 이내면 같은 줄로 묶어 중복 제거가 정확히 동작합니다.

#### 2. 테이블 추출 엔진 교체 (`web_viewer.py`)

```python
# 기존: pdfplumber (pdfminer 기반) → 폰트 인코딩 버그로 중복 발생
with pdfplumber.open(pdf_path) as pdf:
    tables = page.extract_tables()

# 수정: PyMuPDF find_tables() → 중복 없이 정확 추출
doc = fitz.open(stream=data, filetype="pdf")
tabs = page.find_tables()
extracted = table.extract()
```

PyMuPDF의 `find_tables()`는 rawdict 기반으로 동작하여 CID 폰트의 ToUnicode 중복 버그를 근본적으로 회피합니다. 후처리(replace)가 아니라 **파싱 엔진 자체를 교체**한 것입니다.

#### 3. CJK 중복 후처리 보완 (텍스트용 안전장치)

폰트 인코딩 버그가 텍스트 추출에도 영향을 줄 경우를 대비해, `has_cjk_duplication()`으로 감지 후 `fix_cjk_duplication()`으로 한글 연속 중복을 제거하는 로직을 추가했습니다.

#### 4. DB 재파싱 기능 추가

코드를 수정해도 이미 DB에 저장된 데이터는 바뀌지 않으므로, 재파싱 엔드포인트를 추가했습니다.

- `/reparse/<doc_id>` — 개별 문서 재파싱 (PDF 파일은 유지, DB만 갱신)
- `/reparse-all` — 전체 문서 재파싱

### 결과

```
✅ 3 이 법은 가능한 한 외국기업의 스위스 내 사업장에서 근로하는 근로자에게도 적용하도록 한다.
✅ Geltungsbereich Das Gesetz ist unter Vorbehalt
```

`content`와 `table_html` 모두 중복 없이 정상 표시됩니다.
