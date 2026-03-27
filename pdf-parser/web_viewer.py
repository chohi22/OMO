#!/usr/bin/env python3

import glob
import html
import importlib
import io
import os
import re
import shutil
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

import fitz
import psycopg2
from PIL import Image
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)
from markupsafe import Markup, escape
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

pdfplumber = importlib.import_module("pdfplumber")
flask_login = importlib.import_module("flask_login")
LoginManager = flask_login.LoginManager
UserMixin = flask_login.UserMixin
current_user = flask_login.current_user
login_required = flask_login.login_required
login_user = flask_login.login_user
logout_user = flask_login.logout_user

try:
    pytesseract = importlib.import_module("pytesseract")
except Exception:
    pytesseract = None

app = Flask(__name__)
app.secret_key = "pdf-viewer-secret"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

PDF_DIR = "/data/omo/pdf"
UPLOAD_DIR = "/data/omo/pdf"
ALLOWED_EXTENSIONS = {"pdf"}

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "appdb",
    "user": "mobigen",
    "password": "mobigen1!",
}

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None and pytesseract is not None
_DEFAULT_ADMIN_READY = False


class User(UserMixin):
    def __init__(self, user_id: int, username: str, password_hash: str, is_active: bool = True):
        self.id = str(user_id)
        self.username = username
        self.password_hash = password_hash
        self.active = is_active

    @property
    def is_active(self) -> bool:
        return bool(self.active)


@contextmanager
def get_db(timeout: int = 5):
    conn = None
    try:
        conn = psycopg2.connect(connect_timeout=timeout, **DB_CONFIG)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_pdf_datetime(value: object | None) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        value = str(value)
    raw = value.strip()
    if raw.startswith("D:"):
        raw = raw[2:]
    raw = re.sub(r"[^0-9]", "", raw)
    if len(raw) < 4:
        return None
    raw = (raw + "00000000000000")[:14]
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def api_login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "authentication required"}), 401
        return view_func(*args, **kwargs)

    return wrapped


def get_user_by_id(user_id: str) -> User | None:
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id, username, password_hash, is_active FROM pdf_users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return User(row["id"], row["username"], row["password_hash"], row["is_active"])


def get_user_by_username(username: str) -> User | None:
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id, username, password_hash, is_active FROM pdf_users WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return User(row["id"], row["username"], row["password_hash"], row["is_active"])


def ensure_default_admin():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM pdf_users")
            user_count = cur.fetchone()[0]
            if user_count == 0:
                cur.execute(
                    """INSERT INTO pdf_users (username, password_hash, is_active)
                       VALUES (%s, %s, %s)""",
                    ("admin", generate_password_hash("admin123"), True),
                )
    except Exception:
        pass


@login_manager.user_loader
def load_user(user_id: str):
    return get_user_by_id(user_id)


def extract_table_html(pdf_path: str, total_pages: int) -> dict[int, str]:
    """Extract tables from PDF using PyMuPDF's find_tables (no CJK duplication)."""
    table_map: dict[int, str] = {}
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
        for i in range(len(doc)):
            page = doc[i]
            tabs = page.find_tables()
            if not tabs.tables:
                continue
            html_parts = []
            for table in tabs.tables:
                extracted = table.extract()
                html_table = ["<table><tbody>"]
                for row in extracted:
                    cells = "".join(
                        f"<td>{html.escape(str(cell or '').replace(chr(0), ''))}</td>"
                        for cell in row
                    )
                    html_table.append(f"<tr>{cells}</tr>")
                html_table.append("</tbody></table>")
                html_parts.append("".join(html_table))
            table_map[i + 1] = "\n".join(html_parts)
        doc.close()
    except Exception as e:
        print(f"[TABLE] Error extracting tables: {e}")
        for page_no in range(1, total_pages + 1):
            table_map.setdefault(page_no, "")
    return table_map


def fix_cjk_duplication(text: str) -> str:
    """Remove systematic CJK character doubling caused by font encoding bugs.

    When a PDF has a broken ToUnicode table, every CJK character gets emitted
    twice: '한한국국어어' → '한국어'.  Non-CJK characters (spaces, digits, Latin,
    punctuation) are typically NOT doubled, so we only collapse consecutive
    identical CJK characters.

    To avoid destroying legitimate doubles like '각각', '등등', we only apply
    this when has_cjk_duplication() confirms systematic doubling.
    """
    if not text:
        return text
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        # CJK Unified Ideographs + Hangul Syllables + common CJK ranges
        if '\uac00' <= ch <= '\ud7a3' or '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
            # If next char is the same CJK char, skip the duplicate
            if i + 1 < len(text) and text[i + 1] == ch:
                result.append(ch)
                i += 2  # skip the pair
                continue
        result.append(ch)
        i += 1
    return ''.join(result)



def has_cjk_duplication(text: str) -> bool:
    """Detect CJK character duplication bugs where every character appears doubled.

    Real CJK font bugs (missing ToUnicode in CID fonts) cause ALL characters to be
    doubled: '한한국국어어' instead of '한국어'. Legitimate Korean words like '각각',
    '등등', '하하' are NOT duplication bugs.

    Detection strategy:
    1. Grammatical particles (은/는/이/가/을/를/에/의) that are doubled are almost
       never legitimate — '은은', '는는', '를를' trigger immediate detection.
    2. When the ratio of consecutively-doubled CJK characters exceeds 35% of all
       CJK characters AND there are at least 5 such pairs, it indicates a systematic
       font-encoding bug rather than a few legitimate doubled words.
    """
    if not text:
        return False

    cjk_chars = re.findall(r'[가-힣]', text)
    total_cjk = len(cjk_chars)
    if total_cjk < 10:
        return False

    # Grammatical particles / functional morphemes that never legitimately double
    particle_doubles = re.findall(r'은은|는는|이이|가가|을을|를를|에에|의의|로로|으으', text)
    if len(particle_doubles) >= 2:
        return True

    # Ratio-based check: systematic doubling affects almost every character
    pairs = re.findall(r'([가-힣])\1', text)
    ratio = len(pairs) / total_cjk
    return len(pairs) >= 5 and ratio > 0.35


def extract_page_text_dedup(page: fitz.Page) -> str:
    """Extract text deduplicating overlapping CJK spans across different blocks.

    Some PDFs encode text as many small overlapping spans in *different blocks*.
    The same character can appear at the end of one block and the start of the
    next at the exact same x/y coordinates, causing characters to print twice:

        block63 '3'         x=324~332
        block67 '3 이 법은'  x=324~385  ← '3' duplicated
        block72 '은 가능한'  x=372~431  ← '은' duplicated

    Fix: collect all characters across all blocks, group by visual line (Y),
    sort by x, and advance a per-line high-water mark — skip any character
    whose centre-x falls at or before that mark.
    """
    raw = page.get_text('rawdict', flags=fitz.TEXT_INHIBIT_SPACES)

    # Collect every character with its position.
    all_chars: list[tuple[float, float, float, float, str]] = []  # (y_top, x0, cx, x1, c)
    for block in raw.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                for ch in span.get('chars', []):
                    c = ch.get('c', '')
                    if not c:
                        continue
                    bbox = ch.get('bbox', (0, 0, 0, 0))
                    cx = (bbox[0] + bbox[2]) / 2
                    all_chars.append((bbox[1], bbox[0], cx, bbox[2], c))

    if not all_chars:
        return ''

    # Cluster characters into visual lines using Y-tolerance grouping.
    # Characters within Y_TOL points of each other belong to the same line.
    Y_TOL = 3.0
    all_chars.sort(key=lambda t: t[0])  # sort by y_top
    lines: list[list[tuple[float, float, float, str]]] = []
    current_line: list[tuple[float, float, float, str]] = []
    current_y = all_chars[0][0]
    for y_top, x0, cx, x1, c in all_chars:
        if abs(y_top - current_y) > Y_TOL:
            if current_line:
                lines.append(current_line)
            current_line = []
            current_y = y_top
        current_line.append((x0, cx, x1, c))
    if current_line:
        lines.append(current_line)

    parts: list[str] = []
    for line_chars in lines:
        chars = sorted(line_chars, key=lambda t: t[0])  # left-to-right
        line_str = ''
        high_x = -1.0
        for x0, cx, x1, c in chars:
            if cx <= high_x:
                continue
            line_str += c
            if x1 > high_x:
                high_x = x1
        if line_str.strip():
            parts.append(line_str)
    return '\n'.join(parts)


def extract_page_text_with_ocr(page: fitz.Page) -> tuple[str, bool]:
    """Extract text with overlap-dedup and automatic OCR fallback."""
    text = extract_page_text_dedup(page)

    # Check if text has CJK duplication issues
    if text and len(text) >= 10 and not has_cjk_duplication(text):
        # Clean extraction, no duplication
        return text, False
    
    # Text has duplication - try to fix it with post-processing first
    if text and has_cjk_duplication(text):
        fixed = fix_cjk_duplication(text)
        if fixed and not has_cjk_duplication(fixed):
            return fixed, False

    # Try OCR if available
    if not TESSERACT_AVAILABLE or pytesseract is None:
        if text:
            # Return post-processed text even if still imperfect
            return fix_cjk_duplication(text) if has_cjk_duplication(text) else text, False
        return "(OCR 미지원 - Tesseract 설치 필요)", True

    try:
        # Render page at higher DPI for better OCR accuracy
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        ocr_raw = pytesseract.image_to_string(image, lang="kor")
        ocr_text = str(ocr_raw).strip() if ocr_raw is not None else ""
        
        if ocr_text and len(ocr_text) >= 5:
            return ocr_text, True
        
        # OCR failed, return original text if available
        if text:
            return text, False
        return "(OCR 결과 없음)", True
        
    except Exception:
        if text:
            return text, False
        return "(OCR 처리 중 오류)", True


def save_pdf_to_db(pdf_path: str) -> int | None:
    try:
        filename = os.path.basename(pdf_path)
        file_size = os.path.getsize(pdf_path)

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        raw_meta = doc.metadata or {}
        metadata = {k: (v.replace("\x00", "") if isinstance(v, str) else v) for k, v in raw_meta.items()}
        table_map = extract_table_html(pdf_path, total_pages)
        pages_data = []
        has_ocr = False

        for i in range(total_pages):
            content, is_ocr = extract_page_text_with_ocr(doc[i])
            has_ocr = has_ocr or is_ocr
            clean_content = (content or "").replace("\x00", "")
            clean_table = table_map.get(i + 1, "").replace("\x00", "")
            pages_data.append(
                {
                    "page_number": i + 1,
                    "content": clean_content if clean_content.strip() else "(빈 페이지)",
                    "table_html": clean_table,
                    "is_ocr": is_ocr,
                }
            )
        doc.close()

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO pdf_documents (
                       filename, file_size, total_pages, title, author, subject,
                       creator, producer, creation_date, mod_date, has_ocr
                   )
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (filename) DO UPDATE
                   SET file_size = EXCLUDED.file_size,
                       total_pages = EXCLUDED.total_pages,
                       title = EXCLUDED.title,
                       author = EXCLUDED.author,
                       subject = EXCLUDED.subject,
                       creator = EXCLUDED.creator,
                       producer = EXCLUDED.producer,
                       creation_date = EXCLUDED.creation_date,
                       mod_date = EXCLUDED.mod_date,
                       has_ocr = EXCLUDED.has_ocr
                   RETURNING id""",
                (
                    filename,
                    file_size,
                    total_pages,
                    metadata.get("title"),
                    metadata.get("author"),
                    metadata.get("subject"),
                    metadata.get("creator"),
                    metadata.get("producer"),
                    parse_pdf_datetime(metadata.get("creationDate")),
                    parse_pdf_datetime(metadata.get("modDate")),
                    has_ocr,
                ),
            )
            doc_id = cur.fetchone()[0]

            cur.execute("DELETE FROM pdf_pages WHERE document_id = %s", (doc_id,))
            for page in pages_data:
                cur.execute(
                    """INSERT INTO pdf_pages (document_id, page_number, content, table_html, is_ocr)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        doc_id,
                        page["page_number"],
                        page["content"],
                        page["table_html"],
                        page["is_ocr"],
                    ),
                )
        return doc_id
    except Exception as e:
        print(f"[DB] Error saving PDF to database: {e}")
        return None


def sync_filesystem_to_db():
    """Sync PDF files from filesystem to DB - run in background thread to avoid blocking."""
    import threading
    
    def do_sync():
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT filename FROM pdf_documents")
                db_files = {row[0] for row in cur.fetchall()}

            for path in glob.glob(os.path.join(PDF_DIR, "*.pdf")):
                name = os.path.basename(path)
                if name not in db_files:
                    try:
                        save_pdf_to_db(path)
                    except Exception as e:
                        print(f"[DB] Error saving {name}: {e}")
        except Exception as e:
            print(f"[DB] Warning: Could not sync filesystem to DB: {e}")
    
    thread = threading.Thread(target=do_sync, daemon=True)
    thread.start()


def get_pdf_list() -> list[dict]:
    try:
        sync_filesystem_to_db()
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """SELECT id, filename, file_size, total_pages, created_at,
                          title, author, subject, creator, producer,
                          creation_date, mod_date, has_ocr
                   FROM pdf_documents
                   ORDER BY filename"""
            )
            rows = cur.fetchall()

        pdfs = []
        for row in rows:
            size_mb = row["file_size"] / (1024 * 1024)
            pdfs.append(
                {
                    "id": row["id"],
                    "name": row["filename"],
                    "path": os.path.join(PDF_DIR, row["filename"]),
                    "size": f"{size_mb:.1f} MB",
                    "pages": row["total_pages"],
                    "has_ocr": row["has_ocr"],
                }
            )
        return pdfs
    except Exception as e:
        print(f"[DB] Warning: Could not fetch PDF list: {e}")
        return []


def get_document_by_filename(filename: str) -> dict | None:
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """SELECT id, filename, file_size, total_pages, created_at,
                          title, author, subject, creator, producer,
                          creation_date, mod_date, has_ocr
                   FROM pdf_documents
                   WHERE filename = %s""",
                (filename,),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[DB] Warning: Could not fetch document by filename: {e}")
        return None


def get_document_by_id(document_id: int) -> dict | None:
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """SELECT id, filename, file_size, total_pages, created_at,
                          title, author, subject, creator, producer,
                          creation_date, mod_date, has_ocr
                   FROM pdf_documents
                   WHERE id = %s""",
                (document_id,),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[DB] Warning: Could not fetch document by id: {e}")
        return None


def get_pages_by_document_id(document_id: int) -> list[dict]:
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """SELECT page_number, content, table_html, is_ocr
                   FROM pdf_pages
                   WHERE document_id = %s
                   ORDER BY page_number""",
                (document_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "number": row["page_number"],
                "text": row["content"],
                "table_html": row.get("table_html") or "",
                "is_ocr": bool(row.get("is_ocr")),
            }
            for row in rows
        ]
    except Exception as e:
        print(f"[DB] Warning: Could not fetch pages: {e}")
        return []


def get_pages_from_db(filename: str) -> list[dict] | None:
    doc = get_document_by_filename(filename)
    if not doc:
        return None
    pages = get_pages_by_document_id(doc["id"])
    return pages if pages else None


def format_document_metadata(doc: dict | None) -> dict:
    if not doc:
        return {}
    creation_dt = doc.get("creation_date")
    modified_dt = doc.get("mod_date")
    return {
        "title": doc.get("title") or "-",
        "author": doc.get("author") or "-",
        "subject": doc.get("subject") or "-",
        "creator": doc.get("creator") or "-",
        "producer": doc.get("producer") or "-",
        "creation_date": creation_dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(creation_dt, datetime) else "-",
        "mod_date": modified_dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(modified_dt, datetime) else "-",
        "has_ocr": bool(doc.get("has_ocr")),
    }


def highlight_matches(text: str, query: str) -> Markup:
    safe_text = escape(text)
    if not query:
        return Markup(safe_text)
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    highlighted = pattern.sub(lambda m: f"<span class='search-highlight'>{m.group(0)}</span>", str(safe_text))
    return Markup(highlighted)


def build_snippet(content: str, query: str, max_len: int = 260) -> str:
    cleaned = " ".join((content or "").split())
    if not cleaned:
        return ""
    if not query:
        return cleaned[:max_len]
    idx = cleaned.lower().find(query.lower())
    if idx == -1:
        return cleaned[:max_len]
    start = max(0, idx - max_len // 3)
    end = min(len(cleaned), idx + len(query) + (max_len // 2))
    snippet = cleaned[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(cleaned):
        snippet += "..."
    return snippet


def search_pages(query: str) -> list[dict]:
    if not query:
        return []
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """SELECT pd.id AS document_id, pd.filename, pp.page_number, pp.content
                   FROM pdf_pages pp
                   JOIN pdf_documents pd ON pd.id = pp.document_id
                   WHERE pp.content ILIKE %s
                   ORDER BY pd.filename, pp.page_number""",
                (f"%{query}%",),
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"[DB] Warning: Could not search pages: {e}")
        return []


def delete_document(document_id: int) -> bool:
    """Delete document from database and filesystem."""
    filename = None
    try:
        with get_db() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT filename FROM pdf_documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
            if not row:
                print(f"[DELETE] Document {document_id} not found in database")
                return False
            filename = row["filename"]
            
            cur.execute("DELETE FROM pdf_pages WHERE document_id = %s", (document_id,))
            pages_deleted = cur.rowcount
            
            cur.execute("DELETE FROM pdf_documents WHERE id = %s", (document_id,))
            doc_deleted = cur.rowcount
            
            print(f"[DELETE] Document {document_id}: {pages_deleted} pages, document deleted: {doc_deleted > 0}")
        
        if filename:
            file_path = os.path.join(PDF_DIR, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"[DELETE] File removed: {file_path}")
                except OSError as e:
                    print(f"[DELETE] Warning: Could not remove file {file_path}: {e}")
        
        return True
        
    except Exception as e:
        print(f"[DELETE] Error deleting document {document_id}: {e}")
        return False


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF Viewer</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #e2e8f0; }
        .glass { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(99, 102, 241, 0.2); }
        .page-card { background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(99, 102, 241, 0.15); transition: all 0.2s ease; }
        .page-card:hover { border-color: rgba(99, 102, 241, 0.4); box-shadow: 0 0 20px rgba(99, 102, 241, 0.1); }
        .page-badge { background: linear-gradient(135deg, #6366f1, #8b5cf6); }
        .pdf-item { transition: all 0.2s ease; }
        .pdf-item:hover { background: rgba(99, 102, 241, 0.1); border-color: rgba(99, 102, 241, 0.4); }
        .pdf-item.active { background: rgba(99, 102, 241, 0.15); border-color: #6366f1; }
        pre { white-space: pre-wrap; word-wrap: break-word; font-family: 'Pretendard', system-ui, -apple-system, sans-serif; line-height: 1.8; }
        .search-highlight { background: rgba(250, 204, 21, 0.4); border-radius: 2px; padding: 0 2px; }
        .upload-zone { border: 2px dashed rgba(99, 102, 241, 0.3); transition: all 0.2s ease; }
        .upload-zone:hover, .upload-zone.dragover { border-color: #6366f1; background: rgba(99, 102, 241, 0.05); }
        .meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
        .meta-item { background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(99, 102, 241, 0.15); border-radius: 8px; padding: 10px; }
        .table-wrap table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        .table-wrap td, .table-wrap th { border: 1px solid rgba(148, 163, 184, 0.4); padding: 6px 8px; font-size: 12px; }
        .table-wrap tr:nth-child(even) { background: rgba(148, 163, 184, 0.08); }
    </style>
</head>
<body class="min-h-screen">
    <header class="glass sticky top-0 z-50 px-6 py-4">
        <div class="max-w-7xl mx-auto flex items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <a href="{{ url_for('index') }}" class="w-10 h-10 rounded-lg page-badge flex items-center justify-center text-white font-bold text-lg no-underline">P</a>
                <div>
                    <h1 class="text-xl font-bold text-white">PDF Viewer</h1>
                    <p class="text-xs text-gray-400">PDF 텍스트 추출 · OCR · 테이블 · 검색</p>
                </div>
            </div>
            <div class="flex items-center gap-3">
                <form action="{{ url_for('search') }}" method="get" class="relative">
                    <input type="text" name="q" value="{{ global_query or '' }}" placeholder="전체 문서 검색..."
                        class="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500 w-72">
                </form>
                {% if current_user.is_authenticated %}
                    <span class="text-sm text-gray-300">{{ current_user.username }}</span>
                    <a href="{{ url_for('logout') }}" class="text-sm bg-gray-700 hover:bg-gray-600 rounded px-3 py-2">로그아웃</a>
                {% else %}
                    <a href="{{ url_for('login') }}" class="text-sm bg-gray-700 hover:bg-gray-600 rounded px-3 py-2">로그인</a>
                    <a href="{{ url_for('register') }}" class="text-sm bg-indigo-600 hover:bg-indigo-500 rounded px-3 py-2">회원가입</a>
                {% endif %}
            </div>
        </div>
    </header>

    <div class="max-w-7xl mx-auto px-6 py-6 flex gap-6">
        <aside class="w-72 flex-shrink-0">
            <div class="glass rounded-xl p-4 sticky top-24 max-h-[calc(100vh-8rem)] flex flex-col">
                <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 flex-shrink-0">PDF 파일 목록</h2>
                <div class="overflow-y-auto flex-1 -mr-2 pr-2" style="max-height: calc(100vh - 16rem);">
                    {% for pdf in pdf_list %}
                    <a href="{{ url_for('view_pdf', filename=pdf.name) }}"
                       class="pdf-item block rounded-lg border border-gray-700 p-3 mb-2 no-underline {{ 'active' if selected_pdf and selected_pdf == pdf.name else '' }}">
                        <div class="flex items-start gap-3">
                            <div class="w-8 h-8 rounded page-badge flex items-center justify-center text-white text-xs font-bold flex-shrink-0 mt-0.5">PDF</div>
                            <div class="min-w-0">
                                <p class="text-sm text-white font-medium truncate">{{ pdf.name }}</p>
                                <p class="text-xs text-gray-500 mt-1">{{ pdf.pages }}페이지 · {{ pdf.size }}</p>
                                {% if pdf.has_ocr %}<p class="text-xs text-yellow-300 mt-1">OCR 포함</p>{% endif %}
                            </div>
                        </div>
                    </a>
                    {% endfor %}

                    {% if not pdf_list %}<p class="text-sm text-gray-500 text-center py-4">PDF 파일이 없습니다</p>{% endif %}
                </div>

                <div class="mt-4 pt-4 border-t border-gray-700 flex-shrink-0">
                    {% if current_user.is_authenticated %}
                    <form action="{{ url_for('upload_pdf') }}" method="post" enctype="multipart/form-data" id="uploadForm">
                        <label class="upload-zone rounded-lg p-4 flex flex-col items-center cursor-pointer block" id="dropZone">
                            <span class="text-xs text-gray-400">PDF 업로드</span>
                            <span class="text-xs text-gray-600 mt-1">클릭 또는 드래그</span>
                            <input type="file" name="file" accept=".pdf" class="hidden" onchange="document.getElementById('uploadForm').submit()">
                        </label>
                    </form>
                    {% else %}
                    <p class="text-xs text-gray-400">업로드는 로그인 후 가능합니다.</p>
                    {% endif %}
                </div>
            </div>
        </aside>

        <main class="flex-1 min-w-0">
            {% if pages %}
                <div class="mb-4 flex items-center justify-between gap-2">
                    <div>
                        <h2 class="text-lg font-bold text-white">{{ selected_pdf }}</h2>
                        <span class="text-sm text-gray-400">총 {{ pages|length }}페이지</span>
                    </div>
                    {% if current_user.is_authenticated and selected_doc_id %}
                    <form action="{{ url_for('delete_pdf', doc_id=selected_doc_id) }}" method="post" onsubmit="return confirm('문서를 삭제할까요?');">
                        <button type="submit" class="text-xs bg-red-600 hover:bg-red-500 text-white rounded px-3 py-2">삭제</button>
                    </form>
                    {% endif %}
                </div>

                {% if metadata %}
                <div class="glass rounded-xl p-4 mb-4">
                    <h3 class="text-sm uppercase tracking-wider text-gray-400 mb-3">메타데이터</h3>
                    <div class="meta-grid">
                        <div class="meta-item"><p class="text-xs text-gray-500">Title</p><p class="text-sm text-gray-100">{{ metadata.title }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Author</p><p class="text-sm text-gray-100">{{ metadata.author }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Subject</p><p class="text-sm text-gray-100">{{ metadata.subject }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Creator</p><p class="text-sm text-gray-100">{{ metadata.creator }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Producer</p><p class="text-sm text-gray-100">{{ metadata.producer }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Creation Date</p><p class="text-sm text-gray-100">{{ metadata.creation_date }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">Modified Date</p><p class="text-sm text-gray-100">{{ metadata.mod_date }}</p></div>
                        <div class="meta-item"><p class="text-xs text-gray-500">OCR</p><p class="text-sm text-gray-100">{{ 'Yes' if metadata.has_ocr else 'No' }}</p></div>
                    </div>
                </div>
                {% endif %}

                <div class="space-y-4" id="pagesContainer">
                    {% for page in pages %}
                    <div class="page-card rounded-xl p-5" data-page="{{ page.number }}">
                        <div class="flex items-center gap-3 mb-3 pb-3 border-b border-gray-800">
                            <span class="page-badge text-white text-xs font-bold px-2.5 py-1 rounded-md">{{ page.number }}</span>
                            <span class="text-sm text-gray-400">Page {{ page.number }} of {{ pages|length }}</span>
                            {% if page.is_ocr %}<span class="text-xs bg-yellow-600 text-white rounded px-2 py-1">OCR</span>{% endif %}
                        </div>
                        <pre class="text-sm text-gray-300 page-text">{{ page.text }}</pre>
                        {% if page.table_html %}
                        <div class="table-wrap mt-4 text-gray-200">{{ page.table_html | safe }}</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            {% elif flash_messages %}
            {% else %}
                <div class="glass rounded-xl p-16 text-center">
                    <div class="w-20 h-20 rounded-2xl page-badge flex items-center justify-center text-white text-3xl font-bold mx-auto mb-6">P</div>
                    <h2 class="text-2xl font-bold text-white mb-2">{% if not pdf_list %}PDF 파일을 업로드하세요{% else %}PDF를 선택하세요{% endif %}</h2>
                    <p class="text-gray-400 mb-6">
                        {% if not pdf_list %}
                            왼쪽 사이드바에서 PDF 파일을 업로드하거나<br>드래그 앤 드롭으로 추가할 수 있습니다.
                        {% else %}
                            왼쪽 목록에서 PDF 파일을 선택하면 추출된 텍스트를 확인할 수 있습니다.
                        {% endif %}
                    </p>
                    {% if not pdf_list and not current_user.is_authenticated %}
                        <a href="{{ url_for('login') }}" class="inline-block bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-lg font-medium">
                            로그인하여 업로드하기
                        </a>
                    {% endif %}
                </div>
            {% endif %}

            {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for msg in messages %}
                <div class="glass rounded-xl p-4 mt-4 border-l-4 border-yellow-500"><p class="text-sm text-yellow-200">{{ msg }}</p></div>
                {% endfor %}
            {% endif %}
            {% endwith %}
        </main>
    </div>

    <script>
        const dropZone = document.getElementById('dropZone');
        if (dropZone) {
            ['dragenter', 'dragover'].forEach(evt => {
                dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.add('dragover'); });
            });
            ['dragleave', 'drop'].forEach(evt => {
                dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.remove('dragover'); });
            });
            dropZone.addEventListener('drop', e => {
                const files = e.dataTransfer.files;
                if (files.length && files[0].name.toLowerCase().endsWith('.pdf')) {
                    const input = dropZone.querySelector('input[type=file]');
                    input.files = files;
                    document.getElementById('uploadForm').submit();
                }
            });
        }
    </script>
</body>
</html>
"""


SEARCH_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>검색 결과</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #e2e8f0; }
        .glass { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(99, 102, 241, 0.2); }
        .search-highlight { background: rgba(250, 204, 21, 0.4); border-radius: 2px; padding: 0 2px; }
    </style>
</head>
<body class="min-h-screen">
    <div class="max-w-5xl mx-auto py-8 px-6">
        <div class="glass rounded-xl p-5 mb-4">
            <div class="flex items-center justify-between gap-4">
                <h1 class="text-xl font-bold">검색 결과</h1>
                <a href="{{ url_for('index') }}" class="text-sm bg-gray-700 hover:bg-gray-600 rounded px-3 py-2">메인으로</a>
            </div>
            <form action="{{ url_for('search') }}" method="get" class="mt-3">
                <input type="text" name="q" value="{{ query }}" placeholder="검색어 입력"
                    class="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500">
            </form>
            <p class="text-sm text-gray-400 mt-2">"{{ query }}" · {{ results|length }}건</p>
        </div>

        {% for item in results %}
        <div class="glass rounded-xl p-4 mb-3">
            <div class="flex items-center justify-between">
                <a href="{{ url_for('view_pdf', filename=item.filename) }}" class="text-indigo-300 hover:text-indigo-200 font-medium">{{ item.filename }}</a>
                <span class="text-xs text-gray-400">Page {{ item.page_number }}</span>
            </div>
            <p class="text-sm text-gray-300 mt-2">{{ item.snippet | safe }}</p>
        </div>
        {% endfor %}

        {% if not results and query %}
        <div class="glass rounded-xl p-8 text-center text-gray-400">검색 결과가 없습니다.</div>
        {% endif %}
    </div>
</body>
</html>
"""


LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>로그인</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #e2e8f0; }
        .glass { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(99, 102, 241, 0.2); }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6">
    <div class="glass rounded-xl p-6 w-full max-w-md">
        <h1 class="text-xl font-bold mb-4">로그인</h1>
        <form method="post" class="space-y-3">
            <input type="text" name="username" placeholder="아이디" required class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <input type="password" name="password" placeholder="비밀번호" required class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 rounded px-3 py-2 text-sm">로그인</button>
        </form>
        <p class="text-sm text-gray-400 mt-3">계정이 없나요? <a href="{{ url_for('register') }}" class="text-indigo-300">회원가입</a></p>
        <p class="text-sm text-gray-400 mt-2"><a href="{{ url_for('index') }}" class="text-gray-300">메인으로</a></p>
        {% with messages = get_flashed_messages() %}
        {% if messages %}
            {% for msg in messages %}<p class="text-sm text-yellow-200 mt-2">{{ msg }}</p>{% endfor %}
        {% endif %}
        {% endwith %}
    </div>
</body>
</html>
"""


REGISTER_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>회원가입</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #e2e8f0; }
        .glass { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(99, 102, 241, 0.2); }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6">
    <div class="glass rounded-xl p-6 w-full max-w-md">
        <h1 class="text-xl font-bold mb-4">회원가입</h1>
        <form method="post" class="space-y-3">
            <input type="text" name="username" placeholder="아이디" required class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <input type="password" name="password" placeholder="비밀번호" required class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 rounded px-3 py-2 text-sm">가입하기</button>
        </form>
        <p class="text-sm text-gray-400 mt-3">이미 계정이 있나요? <a href="{{ url_for('login') }}" class="text-indigo-300">로그인</a></p>
        <p class="text-sm text-gray-400 mt-2"><a href="{{ url_for('index') }}" class="text-gray-300">메인으로</a></p>
        {% with messages = get_flashed_messages() %}
        {% if messages %}
            {% for msg in messages %}<p class="text-sm text-yellow-200 mt-2">{{ msg }}</p>{% endfor %}
        {% endif %}
        {% endwith %}
    </div>
</body>
</html>
"""


@app.route("/")
def index():
    pdf_list = get_pdf_list()
    return render_template_string(
        HTML_TEMPLATE,
        pdf_list=pdf_list,
        pages=None,
        selected_pdf=None,
        metadata={},
        selected_doc_id=None,
        global_query="",
    )


@app.route("/view/<path:filename>")
def view_pdf(filename):
    pdf_list = get_pdf_list()

    pages = get_pages_from_db(filename)
    document = get_document_by_filename(filename)
    if pages is None:
        pdf_path = os.path.join(PDF_DIR, filename)
        if not os.path.exists(pdf_path):
            flash(f"파일을 찾을 수 없습니다: {filename}")
            return redirect(url_for("index"))
        save_pdf_to_db(pdf_path)
        pages = get_pages_from_db(filename)
        document = get_document_by_filename(filename)

    return render_template_string(
        HTML_TEMPLATE,
        pdf_list=pdf_list,
        pages=pages,
        selected_pdf=filename,
        metadata=format_document_metadata(document),
        selected_doc_id=document["id"] if document else None,
        global_query="",
    )


@app.route("/search")
def search():
    query = (request.args.get("q") or "").strip()
    rows = search_pages(query)
    results = []
    for row in rows:
        snippet = build_snippet(row.get("content") or "", query)
        results.append(
            {
                "document_id": row["document_id"],
                "filename": row["filename"],
                "page_number": row["page_number"],
                "snippet": highlight_matches(snippet, query),
            }
        )
    return render_template_string(SEARCH_TEMPLATE, query=query, results=results)


@app.route("/upload", methods=["POST"])
@login_required
def upload_pdf():
    if "file" not in request.files:
        flash("파일이 선택되지 않았습니다.")
        return redirect(url_for("index"))

    file = request.files["file"]
    incoming_name = file.filename or ""
    if incoming_name == "":
        flash("파일이 선택되지 않았습니다.")
        return redirect(url_for("index"))

    if file and allowed_file(incoming_name):
        filename = secure_filename(incoming_name)
        if incoming_name != filename:
            filename = incoming_name
        filepath = os.path.join(UPLOAD_DIR, filename)
        file.save(filepath)
        save_pdf_to_db(filepath)
        flash(f"'{filename}' 업로드 및 DB 저장 완료!")
        return redirect(url_for("view_pdf", filename=filename))

    flash("PDF 파일만 업로드 가능합니다.")
    return redirect(url_for("index"))


@app.route("/delete/<int:doc_id>", methods=["POST"])
@login_required
def delete_pdf(doc_id: int):
    if delete_document(doc_id):
        flash("문서를 삭제했습니다.")
    else:
        flash("삭제할 문서를 찾지 못했습니다.")
    return redirect(url_for("index"))


def _delete_document_db_only(document_id: int) -> bool:
    """Delete document records from DB without removing the PDF file."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM pdf_pages WHERE document_id = %s", (document_id,))
            cur.execute("DELETE FROM pdf_documents WHERE id = %s", (document_id,))
            return cur.rowcount > 0
    except Exception as e:
        print(f"[REPARSE] Error clearing DB for document {document_id}: {e}")
        return False


@app.route("/reparse/<int:doc_id>", methods=["POST"])
@login_required
def reparse_pdf(doc_id: int):
    """Re-extract text from PDF and update DB (fixes dedup issues)."""
    doc = get_document_by_id(doc_id)
    if not doc:
        flash("문서를 찾지 못했습니다.")
        return redirect(url_for("index"))
    filename = doc["filename"]
    pdf_path = os.path.join(PDF_DIR, filename)
    if not os.path.exists(pdf_path):
        flash(f"PDF 파일을 찾을 수 없습니다: {filename}")
        return redirect(url_for("index"))
    _delete_document_db_only(doc_id)
    save_pdf_to_db(pdf_path)
    flash("문서를 다시 파싱했습니다.")
    return redirect(url_for("view_pdf", filename=filename))


@app.route("/reparse-all", methods=["POST"])
@login_required
def reparse_all():
    """Re-extract text from all PDFs and update DB."""
    count = 0
    for path in glob.glob(os.path.join(PDF_DIR, "*.pdf")):
        filename = os.path.basename(path)
        doc = get_document_by_filename(filename)
        if doc:
            _delete_document_db_only(doc["id"])
        save_pdf_to_db(path)
        count += 1
    flash(f"{count}개 문서를 다시 파싱했습니다.")
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = get_user_by_username(username)
        if not user or not check_password_hash(user.password_hash, password):
            flash("아이디 또는 비밀번호가 올바르지 않습니다.")
            return render_template_string(LOGIN_TEMPLATE)
        login_user(user)
        return redirect(url_for("index"))
    return render_template_string(LOGIN_TEMPLATE)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            flash("아이디와 비밀번호를 입력하세요.")
            return render_template_string(REGISTER_TEMPLATE)
        if get_user_by_username(username):
            flash("이미 존재하는 사용자입니다.")
            return render_template_string(REGISTER_TEMPLATE)

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO pdf_users (username, password_hash, is_active)
                   VALUES (%s, %s, %s)""",
                (username, generate_password_hash(password), True),
            )
        flash("회원가입이 완료되었습니다. 로그인하세요.")
        return redirect(url_for("login"))
    return render_template_string(REGISTER_TEMPLATE)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@app.route("/api/documents", methods=["GET"])
def api_documents_list():
    sync_filesystem_to_db()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT id, filename, file_size, total_pages, created_at,
                      title, author, subject, creator, producer,
                      creation_date, mod_date, has_ocr
               FROM pdf_documents
               ORDER BY filename"""
        )
        docs = cur.fetchall()
    return jsonify({"documents": docs}), 200


@app.route("/api/documents/<int:document_id>", methods=["GET"])
def api_document_detail(document_id: int):
    document = get_document_by_id(document_id)
    if not document:
        return jsonify({"error": "document not found"}), 404
    pages = get_pages_by_document_id(document_id)
    return jsonify({"document": document, "pages": pages}), 200


@app.route("/api/documents/<int:document_id>/pages/<int:page_num>", methods=["GET"])
def api_document_page(document_id: int, page_num: int):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT page_number, content, table_html, is_ocr
               FROM pdf_pages
               WHERE document_id = %s AND page_number = %s""",
            (document_id, page_num),
        )
        page = cur.fetchone()
    if not page:
        return jsonify({"error": "page not found"}), 404
    return jsonify({"page": page}), 200


@app.route("/api/search", methods=["GET"])
def api_search():
    query = (request.args.get("q") or "").strip()
    results = search_pages(query)
    return jsonify({"query": query, "results": results}), 200


@app.route("/api/documents", methods=["POST"])
@api_login_required
def api_upload_document():
    if "file" not in request.files:
        return jsonify({"error": "file field is required"}), 400

    file = request.files["file"]
    incoming_name = file.filename or ""
    if incoming_name == "":
        return jsonify({"error": "filename is empty"}), 400
    if not allowed_file(incoming_name):
        return jsonify({"error": "only pdf files are allowed"}), 400

    filename = secure_filename(incoming_name)
    if incoming_name != filename:
        filename = incoming_name
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)
    doc_id = save_pdf_to_db(filepath)
    return jsonify({"message": "uploaded", "document_id": doc_id, "filename": filename}), 201


@app.route("/api/documents/<int:document_id>", methods=["DELETE"])
@api_login_required
def api_delete_document(document_id: int):
    if not delete_document(document_id):
        return jsonify({"error": "document not found"}), 404
    return jsonify({"message": "deleted", "document_id": document_id}), 200


def ensure_db_tables():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pdf_documents (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) UNIQUE NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    total_pages INTEGER DEFAULT 0,
                    title VARCHAR(500),
                    author VARCHAR(255),
                    subject VARCHAR(500),
                    creator VARCHAR(255),
                    producer VARCHAR(255),
                    creation_date TIMESTAMP,
                    mod_date TIMESTAMP,
                    has_ocr BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pdf_pages (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES pdf_documents(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    content TEXT,
                    table_html TEXT,
                    is_ocr BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(document_id, page_number)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pdf_users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            print("[DB] Tables initialized successfully")
            
    except Exception as e:
        print(f"[DB] Warning: Could not initialize tables: {e}")


if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    try:
        ensure_db_tables()
        ensure_default_admin()
    except Exception:
        pass
    print("\n  PDF Viewer running at: http://localhost:5000\n")
    print(f"  PDF directory: {PDF_DIR}")
    print(f"  Database: {DB_CONFIG['dbname']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
