# sachas_casting_manager_sqlite_with_sessions_bulk.py
import streamlit as st
import sqlite3
import json
import os
import io
import base64
import time
import uuid
import shutil
import re
import tempfile
from datetime import datetime, date
from docx import Document
from docx.shared import Inches
from PIL import Image, UnidentifiedImageError
import hashlib
from contextlib import contextmanager

# ========================
# Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager (SQLite + Sessions)", layout="wide")

DB_FILE = "data.db"
USERS_JSON = "users.json"   # used only for migration
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
DEFAULT_PROJECT_NAME = "Default Project"

# SQLite pragmas
PRAGMA_WAL = "WAL"
PRAGMA_SYNCHRONOUS = "NORMAL"

# ========================
# Small UI CSS tweaks (hide header/footer/shortcuts and tighten top spacing)
# ========================
st.markdown("""
<style>
/* remove Streamlit header & footer & narrow top gap */
header {display:none !important;}
footer {display:none !important;}
.css-18e3th9 {padding-top: 0.4rem;}  /* content top padding - best-effort */
</style>
""", unsafe_allow_html=True)

# ========================
# Inject UI CSS for letter-box participant cards
# ========================
st.markdown("""
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
/* Participant letter-box card */
.participant-letterbox {
  max-width: 520px;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,0.06);
  padding: 8px;
  margin-bottom: 12px;
  background: #fff;
  box-shadow: 0 1px 6px rgba(0,0,0,0.04);
}
.participant-letterbox .photo {
  width: 100%;
  height: 220px;
  display:block;
  object-fit: cover;
  border-radius: 8px;
  background: #f6f6f6;
  margin-bottom: 8px;
}
.participant-letterbox .name {
  font-weight: 700;
  font-size: 1.05rem;
  margin-bottom: 6px;
}
.participant-letterbox .meta {
  color: rgba(0,0,0,0.6);
  font-size: 0.95rem;
  margin-bottom: 4px;
}
.participant-letterbox .small {
  color: rgba(0,0,0,0.55);
  font-size: 0.9rem;
}

/* Grid layout for larger screens: left column card, right small action column */
.part-row {
  display:flex;
  gap:12px;
  align-items:flex-start;
  margin-bottom: 10px;
}

/* Responsive */
@media (max-width: 900px) {
  .participant-letterbox .photo { height: 160px; }
}
@media (max-width: 600px) {
  .participant-letterbox { max-width: 100%; padding: 6px; }
  .participant-letterbox .photo { height: 140px; }
  .part-row { flex-direction: column; }
}

/* Buttons slightly larger for touch */
.stButton>button, button {
  padding: .55rem .9rem !important;
  font-size: 0.98rem !important;
}
</style>
""", unsafe_allow_html=True)

# ========================
# Utilities
# ========================
def _sanitize_for_path(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return re.sub(r"[^0-9A-Za-z\-_]+", "_", s)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def ensure_media_dir():
    os.makedirs(MEDIA_DIR, exist_ok=True)

def looks_like_base64_image(s: str) -> bool:
    if not isinstance(s, str):
        return False
    if len(s) < 120:
        return False
    if os.path.exists(s):
        return False
    if re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", s):
        return True
    return False

def safe_field(row_or_dict, key, default=""):
    """
    Safely get a field from sqlite3.Row or from a dict-like object.
    Returns default for missing/None values.
    """
    if row_or_dict is None:
        return default
    try:
        # sqlite3.Row supports mapping access by key (row["col"])
        val = row_or_dict[key]
    except Exception:
        try:
            # dict-like fallback
            val = row_or_dict.get(key, default)
        except Exception:
            val = default
    return val if val is not None else default

# -------------------------
# safe_rerun helper
# -------------------------
def safe_rerun():
    """Try to re-run the Streamlit script without raising an exception if not allowed."""
    try:
        st.experimental_rerun()
        return
    except Exception:
        pass
    try:
        st.rerun()
        return
    except Exception:
        pass
    # As a last resort, toggle a session flag so Streamlit sees state change and re-executes
    st.session_state["_needs_refresh"] = not st.session_state.get("_needs_refresh", False)
    return

# ========================
# DB connection caching (fast)
# ========================
@st.cache_resource
def get_db_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute(f"PRAGMA synchronous = {PRAGMA_SYNCHRONOUS};")
    except Exception:
        pass
    return conn

# ========================
# Image caching helpers
# ========================
@st.cache_data(show_spinner=False)
def image_b64_for_path(path):
    """Return data:<mime>;base64,... for a given file path (cached)."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            b = f.read()
        b64 = base64.b64encode(b).decode("utf-8")
        mime = "image/jpeg"
        try:
            img = Image.open(io.BytesIO(b))
            fmt = (img.format or "").lower()
            if fmt == "png":
                mime = "image/png"
            elif fmt in ("gif",):
                mime = "image/gif"
            elif fmt in ("webp",):
                mime = "image/webp"
        except Exception:
            pass
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

def thumb_path_for(photo_path):
    """Return path to thumbnail if exists, otherwise original path if exists, else None."""
    if not photo_path:
        return None
    base, ext = os.path.splitext(photo_path)
    thumb = f"{base}_thumb.jpg"
    if os.path.exists(thumb):
        return thumb
    if os.path.exists(photo_path):
        return photo_path
    return None

# ========================
# save uploaded file bytes to media/<username>/<project>/<uuid>.<ext>
# (now also creates a small thumbnail for display)
# ========================
def save_photo_file(uploaded_file, username: str, project_name: str, make_thumb=True, thumb_size=(400, 400)) -> str:
    if not uploaded_file:
        return None
    ensure_media_dir()
    user_safe = _sanitize_for_path(username)
    project_safe = _sanitize_for_path(project_name)
    user_dir = os.path.join(MEDIA_DIR, user_safe, project_safe)
    os.makedirs(user_dir, exist_ok=True)
    orig_name = getattr(uploaded_file, "name", None) or ""
    _, ext = os.path.splitext(orig_name)
    ext = ext.lower() if ext else ""
    if not ext:
        typ = getattr(uploaded_file, "type", "") or ""
        if "jpeg" in typ or "jpg" in typ:
            ext = ".jpg"
        elif "png" in typ:
            ext = ".png"
        else:
            ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(user_dir, filename)
    try:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        data = uploaded_file.read()
        with open(path, "wb") as f:
            if isinstance(data, str):
                data = data.encode("utf-8")
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # create thumbnail next to original (jpg)
        if make_thumb:
            try:
                buf = io.BytesIO(data)
                img = Image.open(buf)
                img.thumbnail(thumb_size)
                thumb_name = f"{os.path.splitext(filename)[0]}_thumb.jpg"
                thumb_path = os.path.join(user_dir, thumb_name)
                img.convert("RGB").save(thumb_path, format="JPEG", quality=75)
            except Exception:
                # ignore thumbnail errors
                pass
        return path.replace("\\", "/")
    except Exception:
        return None

def save_photo_bytes(bytes_data: bytes, username: str, project_name: str, ext_hint: str = ".jpg") -> str:
    if not bytes_data:
        return None
    ensure_media_dir()
    user_safe = _sanitize_for_path(username)
    project_safe = _sanitize_for_path(project_name)
    user_dir = os.path.join(MEDIA_DIR, user_safe, project_safe)
    os.makedirs(user_dir, exist_ok=True)
    ext = ".jpg"
    try:
        buf = io.BytesIO(bytes_data)
        buf.seek(0)
        img = Image.open(buf)
        fmt = (img.format or "").lower()
        if fmt in ("jpeg","jpg"):
            ext = ".jpg"
        elif fmt == "png":
            ext = ".png"
        elif fmt == "gif":
            ext = ".gif"
        elif fmt == "webp":
            ext = ".webp"
        else:
            ext = ext_hint if ext_hint.startswith(".") else "."+ext_hint
    except Exception:
        ext = ext_hint if ext_hint.startswith(".") else "."+ext_hint
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(user_dir, filename)
    try:
        with open(path, "wb") as f:
            f.write(bytes_data)
            f.flush()
            os.fsync(f.fileno())
        # create thumbnail
        try:
            buf2 = io.BytesIO(bytes_data)
            img = Image.open(buf2)
            img.thumbnail((400,400))
            thumb_name = f"{os.path.splitext(filename)[0]}_thumb.jpg"
            thumb_path = os.path.join(user_dir, thumb_name)
            img.convert("RGB").save(thumb_path, format="JPEG", quality=75)
        except Exception:
            pass
        return path.replace("\\", "/")
    except Exception:
        return None

def remove_media_file(path: str):
    try:
        if not path:
            return
        if isinstance(path, str) and os.path.exists(path) and os.path.commonpath([os.path.abspath(path), os.path.abspath(MEDIA_DIR)]) == os.path.abspath(MEDIA_DIR):
            os.remove(path)
            # also try removing thumbnail if exists
            base, _ = os.path.splitext(path)
            thumb = f"{base}_thumb.jpg"
            try:
                if os.path.exists(thumb):
                    os.remove(thumb)
            except Exception:
                pass
            # cleanup empty dirs up to MEDIA_DIR
            parent = os.path.dirname(path)
            while parent and os.path.abspath(parent) != os.path.abspath(MEDIA_DIR):
                try:
                    if not os.listdir(parent):
                        os.rmdir(parent)
                        parent = os.path.dirname(parent)
                    else:
                        break
                except Exception:
                    break
    except Exception:
        pass

def get_photo_bytes(photo_field):
    """
    Accepts:
      - local file path -> returns file bytes
      - base64 string -> decodes and returns bytes
      - None/other -> returns None
    """
    if not photo_field:
        return None
    # if path exists, return bytes
    if isinstance(photo_field, str) and os.path.exists(photo_field):
        try:
            with open(photo_field, "rb") as f:
                return f.read()
        except Exception:
            return None
    # if looks like base64 string
    if isinstance(photo_field, str):
        try:
            return base64.b64decode(photo_field)
        except Exception:
            return None
    return None

# ========================
# SQLite helpers
# ========================
def db_connect():
    # keep for compatibility where a short-lived connection is fine
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # enable WAL for better concurrency
    try:
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute(f"PRAGMA synchronous = {PRAGMA_SYNCHRONOUS};")
    except Exception:
        pass
    return conn

@contextmanager
def db_transaction():
    conn = db_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    # Create initial DB file and tables if not present
    if os.path.exists(DB_FILE):
        return
    with db_transaction() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                last_login TEXT
            );
        """)
        c.execute("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        # participants includes session_id (nullable)
        c.execute("""
            CREATE TABLE participants (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                session_id INTEGER,
                number TEXT,
                name TEXT,
                role TEXT,
                age TEXT,
                agency TEXT,
                height TEXT,
                waist TEXT,
                dress_suit TEXT,
                availability TEXT,
                photo_path TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        """)
        # sessions table for project sessions
        c.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                date TEXT,
                description TEXT,
                created_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        """)
        c.execute("""
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                user TEXT,
                action TEXT,
                details TEXT
            );
        """)
        c.execute("CREATE INDEX idx_projects_user ON projects(user_id);")
        c.execute("CREATE INDEX idx_participants_project ON participants(project_id);")
        c.execute("CREATE INDEX idx_sessions_project ON sessions(project_id);")
        conn.commit()

def ensure_schema():
    """
    Add missing columns/tables to existing DB if upgrading from older schema versions.
    This tries to be non-destructive and best-effort.
    """
    try:
        conn = db_connect()
        cur = conn.cursor()
        # ensure participants.session_id exists
        cur.execute("PRAGMA table_info(participants);")
        cols = [r["name"] for r in cur.fetchall()]
        if "session_id" not in cols:
            try:
                cur.execute("ALTER TABLE participants ADD COLUMN session_id INTEGER;")
            except Exception:
                pass
        # ensure sessions table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions';")
        if not cur.fetchone():
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        date TEXT,
                        description TEXT,
                        created_at TEXT,
                        FOREIGN KEY (project_id) REFERENCES projects(id)
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);")
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ------------------------
# log_action - needed early
# ------------------------
def log_action(user, action, details=""):
    """Insert a log row into logs table. Best-effort: quietly ignore on failure."""
    try:
        with db_transaction() as conn:
            conn.execute(
                "INSERT INTO logs (timestamp, user, action, details) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(), user, action, details)
            )
    except Exception:
        pass

# ========================
# Migration from users.json
# ========================
def migrate_from_json_if_needed():
    if os.path.exists(MIGRATION_MARKER):
        return
    if not os.path.exists(USERS_JSON):
        try:
            ensure_media_dir()
            with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
                f.write(f"no_users_json_at={datetime.now().isoformat()}\n")
        except Exception:
            pass
        return

    try:
        with open(USERS_JSON, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception:
        users = {}

    if not isinstance(users, dict) or not users:
        try:
            ensure_media_dir()
            with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
                f.write(f"empty_or_invalid_users_json_at={datetime.now().isoformat()}\n")
        except Exception:
            pass
        return

    init_db()
    ensure_schema()

    with db_transaction() as conn:
        c = conn.cursor()
        for uname, info in users.items():
            if not isinstance(info, dict):
                continue
            pw = info.get("password") or ""
            role = info.get("role") or "Casting Director"
            last_login = info.get("last_login")
            if pw and len(pw) != 64:
                pw = hash_password(pw)
            if uname == "admin" and pw == "":
                pw = hash_password("supersecret")
                role = "Admin"
            try:
                c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?, ?, ?, ?)",
                          (uname, pw or hash_password(""), role, last_login))
                user_id = c.lastrowid
            except sqlite3.IntegrityError:
                c.execute("SELECT id FROM users WHERE username=?", (uname,))
                row = c.fetchone()
                user_id = row["id"] if row else None
            if user_id:
                projects = info.get("projects", {}) or {}
                if not isinstance(projects, dict) or not projects:
                    projects = {DEFAULT_PROJECT_NAME: {"description":"", "created_at": datetime.now().isoformat(), "participants":[]} }
                for pname, pblock in projects.items():
                    if not isinstance(pblock, dict):
                        continue
                    desc = pblock.get("description", "")
                    created_at = pblock.get("created_at") or datetime.now().isoformat()
                    try:
                        c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?, ?, ?, ?)",
                                  (user_id, pname, desc, created_at))
                        project_id = c.lastrowid
                    except sqlite3.IntegrityError:
                        c.execute("SELECT id FROM projects WHERE user_id=? AND name=?", (user_id, pname))
                        prow = c.fetchone()
                        project_id = prow["id"] if prow else None
                    if project_id:
                        participants = pblock.get("participants", []) or []
                        for entrant in participants:
                            if not isinstance(entrant, dict):
                                continue
                            photo_field = entrant.get("photo")
                            final_path = None
                            if isinstance(photo_field, str) and os.path.exists(photo_field):
                                final_path = photo_field
                            elif looks_like_base64_image(photo_field):
                                try:
                                    bytes_data = base64.b64decode(photo_field)
                                    final_path = save_photo_bytes(bytes_data, uname, pname)
                                except Exception:
                                    final_path = None
                            else:
                                final_path = None
                            c.execute("""
                                INSERT INTO participants
                                (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                project_id,
                                entrant.get("number"),
                                entrant.get("name"),
                                entrant.get("role"),
                                entrant.get("age"),
                                entrant.get("agency"),
                                entrant.get("height"),
                                entrant.get("waist"),
                                entrant.get("dress_suit"),
                                entrant.get("availability"),
                                final_path
                            ))
    try:
        ensure_media_dir()
        with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
            f.write(f"migrated_at={datetime.now().isoformat()}\n")
    except Exception:
        pass

# ========================
# Initialize DB + migrate once
# ========================
init_db()
ensure_schema()
migrate_from_json_if_needed()

# ========================
# Small helpers for app DB ops
# ========================
def get_user_by_username(conn, username):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    return c.fetchone()

def create_user(conn, username, password_hash, role="Casting Director"):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?, ?, ?, ?)",
              (username, password_hash, role, now))
    return c.lastrowid

def update_user_last_login(conn, user_id):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))

def list_projects_for_user(conn, user_id):
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY name COLLATE NOCASE", (user_id,))
    return c.fetchall()

def list_projects_with_counts(conn, user_id):
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.name, p.description, p.created_at,
               COALESCE(cnt.cnt, 0) AS participant_count
        FROM projects p
        LEFT JOIN (
            SELECT project_id, COUNT(*) as cnt
            FROM participants
            GROUP BY project_id
        ) cnt ON cnt.project_id = p.id
        WHERE p.user_id = ?
        ORDER BY p.name COLLATE NOCASE
    """, (user_id,))
    return c.fetchall()

def create_project(conn, user_id, name, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?, ?, ?, ?)",
              (user_id, name, description, now))
    return c.lastrowid

def get_project_by_name(conn, user_id, name):
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE user_id=? AND name=?", (user_id, name))
    return c.fetchone()

def rename_project_move_media(old_name, new_name, username):
    old_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(username), _sanitize_for_path(old_name))
    new_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(username), _sanitize_for_path(new_name))
    try:
        if os.path.exists(old_dir):
            os.makedirs(new_dir, exist_ok=True)
            for f in os.listdir(old_dir):
                oldp = os.path.join(old_dir, f)
                newp = os.path.join(new_dir, f)
                try:
                    shutil.move(oldp, newp)
                except Exception:
                    pass
            try:
                if not os.listdir(old_dir):
                    os.rmdir(old_dir)
            except Exception:
                pass
    except Exception:
        pass

def delete_project_media(username, project_name):
    proj_media_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(username), _sanitize_for_path(project_name))
    try:
        if os.path.exists(proj_media_dir):
            shutil.rmtree(proj_media_dir)
    except Exception:
        pass

# ========================
# Session helpers (sessions within a project)
# ========================
def create_session(conn, project_id, name, date_iso=None, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO sessions (project_id, name, date, description, created_at) VALUES (?, ?, ?, ?, ?)",
              (project_id, name, date_iso, description, now))
    return c.lastrowid

def list_sessions_for_project(conn, project_id):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY date, name", (project_id,))
    return c.fetchall()

def get_session_by_name(conn, project_id, name):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE project_id=? AND name=?", (project_id, name))
    return c.fetchone()

def get_session_by_id(conn, sid):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (sid,))
    return c.fetchone()

def delete_session(conn, session_id):
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

# ========================
# UI: Auth
# ========================
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "current_project_name" not in st.session_state:
    st.session_state["current_project_name"] = None
if "participant_mode" not in st.session_state:
    st.session_state["participant_mode"] = False
if "editing_project" not in st.session_state:
    st.session_state["editing_project"] = None
if "confirm_delete_project" not in st.session_state:
    st.session_state["confirm_delete_project"] = None
if "_needs_refresh" not in st.session_state:
    st.session_state["_needs_refresh"] = False
if "prefill_username" not in st.session_state:
    st.session_state["prefill_username"] = ""
if "bulk_selection" not in st.session_state:
    st.session_state["bulk_selection"] = set()
if "last_bulk_action" not in st.session_state:
    st.session_state["last_bulk_action"] = None
if "bulk_pending" not in st.session_state:
    st.session_state["bulk_pending"] = None
if "current_view_session_id" not in st.session_state:
    st.session_state["current_view_session_id"] = None

if not st.session_state["logged_in"]:
    st.title("🎬 Sacha's Casting Manager")
    choice = st.radio("Choose an option", ["Login", "Sign Up"], horizontal=True)

    if choice == "Login":
        # prefill username if just signed up
        username = st.text_input("Username", value=st.session_state.get("prefill_username", ""))
        # clear prefill after showing it once (so it doesn't persist forever)
        if st.session_state.get("prefill_username"):
            st.session_state["prefill_username"] = ""
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")
        if login_btn:
            # admin backdoor
            if username == "admin" and password == "supersecret":
                with db_transaction() as conn:
                    user = get_user_by_username(conn, "admin")
                    if not user:
                        create_user(conn, "admin", hash_password("supersecret"), role="Admin")
                    else:
                        conn.execute("UPDATE users SET role=?, password=? WHERE username=?", ("Admin", hash_password("supersecret"), "admin"))
                    log_action("admin", "login", "backdoor")
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = "admin"
                st.success("Logged in as Admin ✅")
                safe_rerun()
            # normal login
            try:
                conn = db_connect()
                user = get_user_by_username(conn, username)
                conn.close()
            except Exception:
                user = None
            if user and user["password"] == hash_password(password):
                with db_transaction() as conn:
                    update_user_last_login(conn, user["id"])
                    log_action(username, "login", "normal")
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                st.success(f"Welcome back {username}!")
                safe_rerun()
            else:
                st.error("Invalid credentials")
    else:
        # Signup using a form to make submission reliable
        with st.form("signup_form"):
            new_user = st.text_input("New Username")
            new_pass = st.text_input("New Password", type="password")
            role = st.selectbox("Role", ["Casting Director", "Assistant"])
            signup_btn = st.form_submit_button("Sign Up")

        if signup_btn:
            if not new_user or not new_pass:
                st.error("Please provide a username and password")
            else:
                try:
                    with db_transaction() as conn:
                        existing = get_user_by_username(conn, new_user)
                        if existing:
                            st.error("Username already exists")
                        else:
                            create_user(conn, new_user, hash_password(new_pass), role=role)
                            log_action(new_user, "signup", role)
                            # prefill login with the created username for convenience
                            st.session_state["prefill_username"] = new_user
                            # show confirmation but DO NOT rerun immediately so user sees the message
                            st.success("Account created! Please log in.")
                except Exception as e:
                    st.error(f"Unable to create account: {e}")

# ========================
# After login: main app
# ========================
else:
    current_username = st.session_state["current_user"]
    try:
        conn_temp = db_connect()
        user_row = get_user_by_username(conn_temp, current_username)
        conn_temp.close()
    except Exception:
        user_row = None

    if not user_row:
        st.error("User not found. Log in again.")
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        safe_rerun()

    user_id = user_row["id"]
    role = user_row["role"] or "Casting Director"

    # Sidebar
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as **{current_username}**")
    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["current_project_name"] = None
        st.session_state["current_view_session_id"] = None
        safe_rerun()

    st.sidebar.subheader("Modes")
    try:
        st.session_state["participant_mode"] = st.sidebar.toggle("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))
    except Exception:
        st.session_state["participant_mode"] = st.sidebar.checkbox("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))

    # load user's projects (use cached connection + batched counts)
    conn_read = get_db_conn()
    proj_rows = list_projects_with_counts(conn_read, user_id)
    if not proj_rows:
        with db_transaction() as conn:
            create_project(conn, user_id, DEFAULT_PROJECT_NAME, "")
        conn_read = get_db_conn()
        proj_rows = list_projects_with_counts(conn_read, user_id)

    current_project_name = st.session_state.get("current_project_name")
    project_names = [r["name"] for r in proj_rows]
    if current_project_name not in project_names:
        st.session_state["current_project_name"] = project_names[0] if project_names else DEFAULT_PROJECT_NAME

    active = st.session_state["current_project_name"]
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    st.sidebar.write(f"**{active}**")

    # Participant kiosk
    if st.session_state["participant_mode"]:
        st.title("👋 Casting Check-In")
        st.caption("Fill in your details. Submissions go to the active project.")
        st.info(f"Submitting to project: **{active}**")
        with st.form("participant_form"):
            number = st.text_input("Number")
            name = st.text_input("Name")
            role_in = st.text_input("Role")
            age = st.text_input("Age")
            agency = st.text_input("Agency")
            height = st.text_input("Height")
            waist = st.text_input("Waist")
            dress_suit = st.text_input("Dress/Suit")
            availability = st.text_input("Next Availability")
            photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
            submitted = st.form_submit_button("Submit")
            if submitted:
                with db_transaction() as conn:
                    proj = get_project_by_name(conn, user_id, active)
                    if not proj:
                        pid = create_project(conn, user_id, active, "")
                    else:
                        pid = proj["id"]
                    photo_path = save_photo_file(photo, current_username, active) if photo else None
                    conn.execute("""
                        INSERT INTO participants
                        (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pid, None, number, name, role_in, age, agency, height, waist, dress_suit, availability, photo_path))
                    log_action(current_username, "participant_checkin", name)
                st.success("✅ Thanks for checking in!")
                safe_rerun()

    # Casting manager mode
    else:
        st.title("🎬 Sacha's Casting Manager")

        # Project Manager UI
        st.header("📁 Project Manager")
        pm_col1, pm_col2 = st.columns([3,2])
        with pm_col1:
            query = st.text_input("Search projects by name or description")
        with pm_col2:
            sort_opt = st.selectbox("Sort by", ["Name A→Z", "Newest", "Oldest", "Most Participants", "Fewest Participants"], index=0)

        # Create project
        with st.expander("➕ Create New Project", expanded=False):
            with st.form("new_project_form"):
                p_name = st.text_input("Project Name")
                p_desc = st.text_area("Description", height=80)
                create_btn = st.form_submit_button("Create Project")
                if create_btn:
                    if not p_name:
                        st.error("Provide a project name")
                    else:
                        try:
                            with db_transaction() as conn:
                                existing = get_project_by_name(conn, user_id, p_name)
                                if existing:
                                    st.error("Project with this name exists")
                                else:
                                    create_project(conn, user_id, p_name, p_desc or "")
                                    log_action(current_username, "create_project", p_name)
                                    st.success(f"Project '{p_name}' created.")
                                    # set active project so later UI shows it
                                    st.session_state["current_project_name"] = p_name
                        except Exception as e:
                            st.error(f"Unable to create project: {e}")

        # fetch fresh projects (batched counts)
        conn_read = get_db_conn()
        proj_rows = list_projects_with_counts(conn_read, user_id)
        proj_items = []
        for r in proj_rows:
            proj_items.append((r["name"], r["description"], r["created_at"], r["participant_count"]))

        if query:
            q = query.lower().strip()
            proj_items = [x for x in proj_items if q in x[0].lower() or q in (x[1] or "").lower()]

        if sort_opt == "Name A→Z":
            proj_items.sort(key=lambda x: x[0].lower())
        elif sort_opt == "Newest":
            proj_items.sort(key=lambda x: x[2], reverse=True)
        elif sort_opt == "Oldest":
            proj_items.sort(key=lambda x: x[2])
        elif sort_opt == "Most Participants":
            proj_items.sort(key=lambda x: x[3], reverse=True)
        elif sort_opt == "Fewest Participants":
            proj_items.sort(key=lambda x: x[3])

        # header
        hdr = st.columns([3,4,2,2,4])
        hdr[0].markdown("**Project**"); hdr[1].markdown("**Description**"); hdr[2].markdown("**Created**"); hdr[3].markdown("**Participants**"); hdr[4].markdown("**Actions**")

        for name, desc, created, count in proj_items:
            is_active = (name == st.session_state.get("current_project_name"))
            cols = st.columns([3,4,2,2,4])
            cols[0].markdown(f"{'🟢 ' if is_active else ''}**{name}**")
            cols[1].markdown(desc or "—")
            cols[2].markdown((created or "").split("T")[0])
            cols[3].markdown(str(count))
            a1, a2, a3 = cols[4].columns([1,1,1])
            if a1.button("Set Active", key=f"setactive_{name}"):
                st.session_state["current_project_name"] = name
                safe_rerun()
            if a2.button("Edit", key=f"editproj_{name}"):
                st.session_state["editing_project"] = name
                safe_rerun()
            if a3.button("Delete", key=f"delproj_{name}"):
                st.session_state["confirm_delete_project"] = name
                safe_rerun()

            # inline edit
            if st.session_state.get("editing_project") == name:
                with st.form(f"edit_project_form_{name}"):
                    new_name = st.text_input("Project Name", value=name)
                    new_desc = st.text_area("Description", value=desc, height=100)
                    c1,c2 = st.columns(2)
                    save_changes = c1.form_submit_button("Save")
                    cancel_edit = c2.form_submit_button("Cancel")
                    if save_changes:
                        if not new_name:
                            st.error("Name cannot be empty")
                        else:
                            try:
                                with db_transaction() as conn:
                                    proj = get_project_by_name(conn, user_id, name)
                                    if not proj:
                                        st.error("Project not found")
                                    else:
                                        conn.execute("UPDATE projects SET name=?, description=? WHERE id=?", (new_name, new_desc, proj["id"]))
                                        rename_project_move_media(name, new_name, current_username)
                                        log_action(current_username, "edit_project", f"{name} -> {new_name}")
                                st.success("Project updated.")
                                st.session_state["editing_project"] = None
                                if st.session_state.get("current_project_name") == name:
                                    st.session_state["current_project_name"] = new_name
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to save project: {e}")
                    if cancel_edit:
                        st.session_state["editing_project"] = None
                        safe_rerun()

            # delete confirmation
            if st.session_state.get("confirm_delete_project") == name:
                st.warning(f"Type project name **{name}** to confirm deletion. This cannot be undone.")
                with st.form(f"confirm_delete_{name}"):
                    confirm_text = st.text_input("Confirm name")
                    d1,d2 = st.columns(2)
                    do_delete = d1.form_submit_button("Delete Permanently")
                    cancel_delete = d2.form_submit_button("Cancel")
                    if do_delete:
                        if confirm_text == name:
                            try:
                                with db_transaction() as conn:
                                    proj = get_project_by_name(conn, user_id, name)
                                    if not proj:
                                        st.error("Project not found")
                                    else:
                                        pid = proj["id"]
                                        c = conn.cursor()
                                        c.execute("SELECT photo_path FROM participants WHERE project_id=?", (pid,))
                                        rows = c.fetchall()
                                        for r in rows:
                                            pf = r["photo_path"]
                                            if isinstance(pf, str) and os.path.exists(pf):
                                                remove_media_file(pf)
                                        c.execute("DELETE FROM participants WHERE project_id=?", (pid,))
                                        c.execute("DELETE FROM projects WHERE id=?", (pid,))
                                        delete_project_media(current_username, name)
                                        log_action(current_username, "delete_project", name)
                                st.success(f"Project '{name}' deleted.")
                                if st.session_state.get("current_project_name") == name:
                                    st.session_state["current_project_name"] = None
                                st.session_state["confirm_delete_project"] = None
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to delete project: {e}")
                        else:
                            st.error("Project name mismatch. Not deleted.")
                    if cancel_delete:
                        st.session_state["confirm_delete_project"] = None
                        safe_rerun()

        # ------------------------
        # Sessions Manager (separate from participants)
        # ------------------------
        st.header("🗂️ Sessions (within project)")
        sess_col_left, sess_col_right = st.columns([3,1])
        with sess_col_left:
            st.subheader("Sessions")
            with db_connect() as conn:
                proj = get_project_by_name(conn, user_id, active)
            if not proj:
                st.info("Project missing — create project first.")
            else:
                project_id = proj["id"]
                with db_connect() as conn:
                    sessions = list_sessions_for_project(conn, project_id)
                if not sessions:
                    st.info("No sessions yet for this project.")
                else:
                    for s in sessions:
                        row_cols = st.columns([4,1,1])
                        name_display = f"**{s['name']}**"
                        if s['date']:
                            try:
                                dstr = s['date'].split("T")[0] if "T" in s['date'] else s['date']
                            except Exception:
                                dstr = s['date']
                            name_display += f" — {dstr}"
                        row_cols[0].markdown(name_display)
                        if row_cols[1].button("View", key=f"view_sess_{s['id']}"):
                            # set current view to this session
                            st.session_state["current_view_session_id"] = s["id"]
                            safe_rerun()
                        if row_cols[2].button("Delete", key=f"del_sess_{s['id']}"):
                            # confirm delete via form inline
                            confirm = st.text_input(f"Type DELETE to confirm deletion of {s['name']}", key=f"confirm_del_sess_{s['id']}")
                            if confirm == "DELETE":
                                try:
                                    with db_transaction() as conn:
                                        # move participants in this session to Unassigned (NULL) first and then delete session
                                        conn.execute("UPDATE participants SET session_id=NULL WHERE session_id=?", (s["id"],))
                                        delete_session(conn, s["id"])
                                        log_action(current_username, "delete_session", s["name"])
                                    st.success(f"Session '{s['name']}' deleted and participants unassigned.")
                                    st.session_state["current_view_session_id"] = None
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Unable to delete session: {e}")
        with sess_col_right:
            st.subheader("Create Session")
            with st.form("create_session_form"):
                sess_name = st.text_input("Session name")
                sess_date = st.date_input("Session date", value=date.today())
                sess_desc = st.text_area("Description", height=80)
                create_sess_btn = st.form_submit_button("Create Session")
                if create_sess_btn:
                    if not sess_name:
                        st.error("Please provide a session name")
                    else:
                        try:
                            with db_transaction() as conn:
                                create_session(conn, project_id, sess_name, sess_date.isoformat(), sess_desc or "")
                                log_action(current_username, "create_session", sess_name)
                            st.success(f"Session '{sess_name}' created.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to create session: {e}")

        # ------------------------
        # Participant management UI
        # ------------------------
        current = st.session_state["current_project_name"]
        with db_connect() as conn:
            proj = get_project_by_name(conn, user_id, current)
        if not proj:
            with db_transaction() as conn:
                create_project(conn, user_id, current, "")
            with db_connect() as conn:
                proj = get_project_by_name(conn, user_id, current)

        project_id = proj["id"]

        st.header(f"👥 Participants — {current}")

        # Add new participant
        with st.expander("➕ Add New Participant"):
            with st.form("add_participant"):
                number = st.text_input("Number")
                pname = st.text_input("Name")
                prole = st.text_input("Role")
                page = st.text_input("Age")
                pagency = st.text_input("Agency")
                pheight = st.text_input("Height")
                pwaist = st.text_input("Waist")
                pdress = st.text_input("Dress/Suit")
                pavail = st.text_input("Next Availability")
                # choose session assignment on add
                with db_connect() as conn:
                    sess_rows = list_sessions_for_project(conn, project_id)
                sess_options = ["Unassigned"] + [s["name"] for s in sess_rows]
                assign_to = st.selectbox("Assign to session", sess_options, index=0)
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                submitted = st.form_submit_button("Add Participant")
                if submitted:
                    try:
                        with db_transaction() as conn:
                            # resolve session id if chosen
                            target_sid = None
                            if assign_to != "Unassigned":
                                srec = get_session_by_name(conn, project_id, assign_to)
                                target_sid = srec["id"] if srec else None
                            photo_path = save_photo_file(photo, current_username, current) if photo else None
                            conn.execute("""
                                INSERT INTO participants
                                (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, target_sid, number, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, photo_path))
                            log_action(current_username, "add_participant", pname)
                        st.success("Participant added!")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to add participant: {e}")

        # list participants (letter-box style with photo on top, details below)
        # support viewing a specific session or all participants
        with db_connect() as conn:
            cur = conn.cursor()
            view_sid = st.session_state.get("current_view_session_id")
            if view_sid:
                # show only participants in the selected session
                cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY id", (project_id, view_sid))
            else:
                cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
            participants = cur.fetchall()
            # load sessions map for name lookup
            cur.execute("SELECT id, name FROM sessions WHERE project_id=?", (project_id,))
            sess_map_rows = cur.fetchall()
            session_name_map = {r["id"]: r["name"] for r in sess_map_rows}

        if view_sid:
            st.info(f"Showing participants assigned to session: **{session_name_map.get(view_sid,'(unknown)')}** — [Click 'View All' to see entire project]")
            if st.button("View All Participants"):
                st.session_state["current_view_session_id"] = None
                safe_rerun()
        else:
            # small control to view sessions quickly
            with st.expander("Quick: Jump to a session"):
                with db_connect() as conn:
                    sess_rows = list_sessions_for_project(conn, project_id)
                if sess_rows:
                    cols = st.columns([2]*3)
                    i = 0
                    for s in sess_rows:
                        if cols[i % 3].button(f"View: {s['name']}", key=f"quick_view_sess_{s['id']}"):
                            st.session_state["current_view_session_id"] = s["id"]
                            safe_rerun()
                        i += 1
                else:
                    st.write("No sessions created yet.")

        if not participants:
            st.info("No participants yet.")
        else:
            # Provide small filter box for visible selection and faster selection
            vis_filter = st.text_input("Filter visible participants (name / role / agency)", value="", key="visible_part_filter")
            # Render participants in a grid-like flow
            for p in participants:
                # basic filtering for visible list
                if vis_filter:
                    q = vis_filter.lower().strip()
                    if q not in (safe_field(p, "name","") or "").lower() and q not in (safe_field(p, "role","") or "").lower() and q not in (safe_field(p, "agency","") or "").lower():
                        continue

                pid = p["id"]
                left, right = st.columns([9,1])
                # choose thumbnail or original
                display_path = thumb_path_for(safe_field(p, "photo_path",""))
                data_uri = image_b64_for_path(display_path) if display_path else None
                if data_uri:
                    img_tag = f"<img class='photo' src='{data_uri}' alt='photo'/>"
                else:
                    img_tag = "<div class='photo' style='display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"

                # Details HTML
                name_html = safe_field(p, "name", "Unnamed")
                number_html = safe_field(p, "number", "")
                role_html = safe_field(p, "role", "")
                age_html = safe_field(p, "age", "")
                agency_html = safe_field(p, "agency", "")
                height_html = safe_field(p, "height", "")
                waist_html = safe_field(p, "waist", "")
                dress_html = safe_field(p, "dress_suit", "")
                avail_html = safe_field(p, "availability", "")
                sess_id = safe_field(p, "session_id", None)
                sess_display = session_name_map.get(sess_id, "Unassigned")

                card_html = f"""
                    <div class="participant-letterbox">
                        {img_tag}
                        <div class="name">{st.safe_format(name_html)} <span class="small">#{st.safe_format(number_html)}</span></div>
                        <div class="meta">Role: {st.safe_format(role_html)} • Age: {st.safe_format(age_html)}</div>
                        <div class="meta">Agency: {st.safe_format(agency_html)}</div>
                        <div class="meta">Height: {st.safe_format(height_html)} • Waist: {st.safe_format(waist_html)} • Dress/Suit: {st.safe_format(dress_html)}</div>
                        <div class="small">Availability: {st.safe_format(avail_html)}</div>
                        <div class="small" style="margin-top:6px;"><strong>Session:</strong> {st.safe_format(sess_display)}</div>
                    </div>
                """
                left.markdown(card_html, unsafe_allow_html=True)

                # Right column: Edit/Delete buttons (keep previous functionality)
                # Selection checkbox for bulk actions
                sel_col = right
                currently_selected = pid in st.session_state["bulk_selection"]
                if st.checkbox("Select", value=currently_selected, key=f"sel_cb_{pid}"):
                    st.session_state["bulk_selection"].add(pid)
                else:
                    if pid in st.session_state["bulk_selection"]:
                        st.session_state["bulk_selection"].discard(pid)

                if st.button("Edit", key=f"edit_{pid}"):
                    with st.form(f"edit_participant_{pid}"):
                        enumber = st.text_input("Number", value=safe_field(p, "number", ""))
                        ename = st.text_input("Name", value=safe_field(p, "name", ""))
                        erole = st.text_input("Role", value=safe_field(p, "role", ""))
                        eage = st.text_input("Age", value=safe_field(p, "age", ""))
                        eagency = st.text_input("Agency", value=safe_field(p, "agency", ""))
                        eheight = st.text_input("Height", value=safe_field(p, "height", ""))
                        ewaist = st.text_input("Waist", value=safe_field(p, "waist", ""))
                        edress = st.text_input("Dress/Suit", value=safe_field(p, "dress_suit", ""))
                        eavail = st.text_input("Next Availability", value=safe_field(p, "availability", ""))
                        # session selector inline
                        with db_connect() as conn:
                            sess_rows = list_sessions_for_project(conn, project_id)
                        sess_options = ["Unassigned"] + [s["name"] for s in sess_rows]
                        current_sid = safe_field(p, "session_id", None)
                        current_sid_name = session_name_map.get(current_sid, "Unassigned")
                        esession = st.selectbox("Session", sess_options, index=sess_options.index(current_sid_name) if current_sid_name in sess_options else 0)
                        ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                        save_edit = st.form_submit_button("Save Changes")
                        cancel_edit = st.form_submit_button("Cancel")
                        if save_edit:
                            try:
                                with db_transaction() as conn:
                                    new_photo_path = safe_field(p, "photo_path", "")
                                    if ephoto:
                                        new_photo_path = save_photo_file(ephoto, current_username, current)
                                        oldphoto = safe_field(p, "photo_path", "")
                                        if isinstance(oldphoto, str) and os.path.exists(oldphoto):
                                            remove_media_file(oldphoto)
                                    # resolve session id
                                    target_sid = None
                                    if esession != "Unassigned":
                                        srec = get_session_by_name(conn, project_id, esession)
                                        target_sid = srec["id"] if srec else None
                                    conn.execute("""
                                        UPDATE participants SET session_id=?, number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?
                                        WHERE id=?
                                    """, (target_sid, enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, pid))
                                    log_action(current_username, "edit_participant", ename)
                                st.success("Participant updated!")
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to save participant edits: {e}")
                        if cancel_edit:
                            safe_rerun()

                if st.button("Delete", key=f"del_{pid}"):
                    try:
                        with db_transaction() as conn:
                            if isinstance(safe_field(p, "photo_path", ""), str) and os.path.exists(safe_field(p, "photo_path", "")):
                                remove_media_file(safe_field(p, "photo_path", ""))
                            conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                            log_action(current_username, "delete_participant", safe_field(p, "name", ""))
                        st.warning("Participant deleted")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to delete participant: {e}")

        # ------------------------
        # Improved Bulk controls
        # ------------------------
        st.markdown("---")
        st.subheader("Bulk actions")

        # quick participant search (filtered selection on the visible list)
        filter_col1, filter_col2, filter_col3 = st.columns([3,1,1])
        with filter_col1:
            bulk_filter = st.text_input("Filter visible participants (name / role / agency)", value="", key="bulk_filter_input")
        with filter_col2:
            if st.button("Select Visible", key="select_visible_btn"):
                visible_ids = []
                for p in participants:
                    # apply same visible filter as earlier
                    if bulk_filter:
                        q = bulk_filter.lower().strip()
                        if q not in (safe_field(p, "name","") or "").lower() and q not in (safe_field(p, "role","") or "").lower() and q not in (safe_field(p, "agency","") or "").lower():
                            continue
                    visible_ids.append(p["id"])
                st.session_state["bulk_selection"].update(visible_ids)
                st.success(f"Selected {len(visible_ids)} visible participants")
                safe_rerun()
        with filter_col3:
            if st.button("Clear Selection", key="clear_bulk_sel"):
                st.session_state["bulk_selection"].clear()
                st.success("Selection cleared")
                safe_rerun()

        # helper controls: select all in visible/project / invert
        ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1,1,1])
        with ctrl_col1:
            if st.button("Select All (visible)", key="select_all_visible"):
                v = []
                for p in participants:
                    if bulk_filter:
                        q = bulk_filter.lower().strip()
                        if q not in (safe_field(p, "name","") or "").lower() and q not in (safe_field(p, "role","") or "").lower() and q not in (safe_field(p, "agency","") or "").lower():
                            continue
                    v.append(p["id"])
                st.session_state["bulk_selection"].update(v)
                st.success(f"Selected {len(v)} participants (visible)")
                safe_rerun()
        with ctrl_col2:
            if st.button("Invert Selection (visible)", key="invert_visible"):
                visible_ids = []
                for p in participants:
                    if bulk_filter:
                        q = bulk_filter.lower().strip()
                        if q not in (safe_field(p, "name","") or "").lower() and q not in (safe_field(p, "role","") or "").lower() and q not in (safe_field(p, "agency","") or "").lower():
                            continue
                    visible_ids.append(p["id"])
                new_sel = set(visible_ids).difference(st.session_state["bulk_selection"])
                st.session_state["bulk_selection"].difference_update(visible_ids)
                st.session_state["bulk_selection"].update(new_sel)
                st.success("Inverted selection for visible participants")
                safe_rerun()
        with ctrl_col3:
            if st.button("Show selection count", key="bulk_count"):
                st.info(f"{len(st.session_state['bulk_selection'])} participants selected")

        # Target session + inline session creation
        with db_connect() as conn:
            sess_rows = list_sessions_for_project(conn, project_id)
        sess_map = {None: "Unassigned"}
        for s in sess_rows:
            sess_map[s["id"]] = s["name"]

        bulk_col_target, bulk_col_mode = st.columns([3,2])
        with bulk_col_target:
            # show a simple selectbox and a create-new toggle
            target_choice = st.selectbox("Target session", options=["Unassigned", "(create new session)"] + [v for k,v in sess_map.items() if k is not None], index=0)
            new_session_name = None
            new_session_date = None
            new_session_desc = ""
            if target_choice == "(create new session)":
                new_session_name = st.text_input("New session name", key="bulk_new_session_name")
                new_session_date = st.date_input("New session date (optional)", key="bulk_new_session_date")
                new_session_desc = st.text_area("New session description (optional)", key="bulk_new_session_desc", height=60)
        with bulk_col_mode:
            bulk_action_mode = st.radio("Action", ["Move (cut)", "Copy"], index=0, horizontal=True, key="bulk_move_or_copy")

        # Apply (two-step confirm)
        apply_col, apply_col2 = st.columns([1,1])
        with apply_col:
            if st.button("Prepare bulk action", key="prepare_bulk"):
                sel_ids = list(st.session_state["bulk_selection"])
                if not sel_ids:
                    st.warning("No participants selected. Select some participants first.")
                else:
                    # resolve or create target session id (defer creating new session until Confirm)
                    target_session_id = None
                    if target_choice == "(create new session)":
                        if not new_session_name:
                            st.error("Provide a name for the new session before preparing.")
                        else:
                            # mark pending with new session info (do not create yet)
                            st.session_state["bulk_pending"] = {
                                "participant_ids": sel_ids,
                                "action": bulk_action_mode,
                                "new_session": {"name": new_session_name, "date": new_session_date.isoformat() if new_session_date else None, "description": new_session_desc},
                                "target_session_id": None,
                                "target_session_name": new_session_name
                            }
                            st.warning(f"Prepared: {bulk_action_mode} {len(sel_ids)} participants -> NEW session '{new_session_name}'. Click CONFIRM to execute.")
                    else:
                        # find existing session id
                        resolved_sid = None
                        if target_choice == "Unassigned":
                            resolved_sid = None
                        else:
                            # find key by value
                            for k,v in sess_map.items():
                                if v == target_choice:
                                    resolved_sid = k
                                    break
                        st.session_state["bulk_pending"] = {
                            "participant_ids": sel_ids,
                            "action": bulk_action_mode,
                            "new_session": None,
                            "target_session_id": resolved_sid,
                            "target_session_name": sess_map.get(resolved_sid, "Unassigned")
                        }
                        st.warning(f"Prepared: {bulk_action_mode} {len(sel_ids)} participants -> session '{sess_map.get(resolved_sid,'Unassigned')}'. Click CONFIRM to execute.")
        with apply_col2:
            if st.button("Cancel prepared action", key="cancel_bulk_pending"):
                st.session_state["bulk_pending"] = None
                st.success("Pending bulk action canceled.")
                safe_rerun()

        # Confirmation area (execute pending)
        if st.session_state.get("bulk_pending"):
            pending = st.session_state["bulk_pending"]
            st.info(f"Pending: {pending['action']} {len(pending['participant_ids'])} participants -> {pending['target_session_name']}")
            confirm_col, confirm_col2 = st.columns([1,1])
            with confirm_col:
                if st.button("CONFIRM bulk action", key="confirm_bulk"):
                    try:
                        with db_transaction() as conn:
                            cur = conn.cursor()
                            sel_ids = pending["participant_ids"]
                            # If creating a session, create it now and get its id
                            if pending["new_session"]:
                                sd = pending["new_session"]
                                target_session_id = create_session(conn, project_id, sd["name"], sd["date"], sd.get("description","") or "")
                                target_session_name = sd["name"]
                                log_action(current_username, "create_session_for_bulk", sd["name"])
                            else:
                                target_session_id = pending["target_session_id"]
                                target_session_name = pending["target_session_name"]
                            # snapshot prev session assignments for undo
                            prev_map = {}
                            cur.execute(f"SELECT id, session_id FROM participants WHERE id IN ({','.join(['?']*len(sel_ids))})", sel_ids)
                            rows = cur.fetchall()
                            for r in rows:
                                prev_map[r["id"]] = r["session_id"]

                            affected = 0
                            if pending["action"] == "Move (cut)":
                                cur.executemany("UPDATE participants SET session_id=? WHERE id=?", [(target_session_id, pid) for pid in sel_ids])
                                affected = len(sel_ids)
                            else:
                                # Copy: duplicate rows for each selected participant with new session assignment
                                for pid in sel_ids:
                                    cur.execute("SELECT project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path FROM participants WHERE id=?", (pid,))
                                    r = cur.fetchone()
                                    if r:
                                        cur.execute("""INSERT INTO participants
                                            (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                            (r["project_id"], target_session_id, r["number"], r["name"], r["role"], r["age"], r["agency"], r["height"], r["waist"], r["dress_suit"], r["availability"], r["photo_path"]))
                                        affected += 1
                            log_action(current_username, "bulk_move_copy", f"{pending['action']} {affected} -> session {target_session_name}")
                            # store undo info in session_state
                            st.session_state["last_bulk_action"] = {
                                "timestamp": time.time(),
                                "action": pending["action"],
                                "participant_ids": sel_ids,
                                "prev_session_map": prev_map,
                                "created_session_id": target_session_id if pending["new_session"] else None
                            }
                            # clear the pending and selection
                            st.session_state["bulk_pending"] = None
                            st.session_state["bulk_selection"].clear()
                        st.success(f"Bulk operation completed: {pending['action']} {affected} participants.")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Bulk operation failed: {e}")
            with confirm_col2:
                if st.button("UNDO last bulk move (if available)", key="undo_bulk"):
                    last = st.session_state.get("last_bulk_action")
                    if not last:
                        st.warning("No bulk action to undo.")
                    else:
                        # allow undo only within short window (e.g. 5 minutes)
                        if time.time() - last.get("timestamp", 0) > 300:
                            st.warning("Undo window expired (over 5 minutes).")
                        else:
                            try:
                                with db_transaction() as conn:
                                    cur = conn.cursor()
                                    if last["action"].startswith("Move"):
                                        # revert session_id for moved participants
                                        for pid, prev_sid in last["prev_session_map"].items():
                                            cur.execute("UPDATE participants SET session_id=? WHERE id=?", (prev_sid, pid))
                                    else:
                                        st.warning("Automatic undo for Copy is not supported. Please manually delete the copied participants if needed.")
                                        raise Exception("Copy undo not performed")
                                    log_action(current_username, "undo_bulk_action", f"Undo of {last['action']}")
                                st.success("Undo applied.")
                                st.session_state["last_bulk_action"] = None
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Undo failed or not supported for this action: {e}")

        # ------------------------
        # Export to Word (supports session view vs all)
        # ------------------------
        st.subheader("📄 Export Participants (Word)")
        if st.button("Download Word File of Current View"):
            try:
                with db_connect() as conn:
                    cur = conn.cursor()
                    view_sid = st.session_state.get("current_view_session_id")
                    if view_sid:
                        cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY id", (project_id, view_sid))
                    else:
                        cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                    parts = cur.fetchall()
                    if not parts:
                        st.info("No participants in this view.")
                    else:
                        doc = Document()
                        heading = f"Participants - {current}"
                        if st.session_state.get("current_view_session_id"):
                            heading += f" - Session: {session_name_map.get(st.session_state.get('current_view_session_id'),'(unknown)')}"
                        doc.add_heading(heading, 0)
                        for p in parts:
                            table = doc.add_table(rows=1, cols=2)
                            table.autofit = False
                            table.columns[0].width = Inches(1.7)
                            table.columns[1].width = Inches(4.5)
                            row_cells = table.rows[0].cells

                            # Prefer thumbnail if available
                            display_path = thumb_path_for(safe_field(p, "photo_path", ""))
                            bytes_data = None
                            # 1) try file bytes (thumb or original)
                            if display_path and os.path.exists(display_path):
                                try:
                                    with open(display_path, "rb") as f:
                                        bytes_data = f.read()
                                except Exception:
                                    bytes_data = None
                            # 2) fallback: try stored path/raw base64
                            if bytes_data is None:
                                bytes_data = get_photo_bytes(safe_field(p, "photo_path", ""))

                            if bytes_data:
                                try:
                                    image_stream = io.BytesIO(bytes_data)
                                    image_stream.seek(0)
                                    paragraph = row_cells[0].paragraphs[0]
                                    run = paragraph.add_run()
                                    try:
                                        # Try adding picture directly from BytesIO
                                        run.add_picture(image_stream, width=Inches(1.5))
                                    except Exception:
                                        # Fallback: write to a temp file and pass the filename
                                        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                                        try:
                                            tf.write(bytes_data)
                                            tf.flush()
                                            tf.close()
                                            run.add_picture(tf.name, width=Inches(1.5))
                                        finally:
                                            try:
                                                os.unlink(tf.name)
                                            except Exception:
                                                pass
                                except Exception:
                                    row_cells[0].text = "Photo Error"
                            else:
                                row_cells[0].text = "No Photo"

                            # Use safe_field to read fields from sqlite3.Row or dict
                            info_text = (
                                f"Number: {safe_field(p, 'number','')}\n"
                                f"Name: {safe_field(p, 'name','')}\n"
                                f"Role: {safe_field(p, 'role','')}\n"
                                f"Age: {safe_field(p, 'age','')}\n"
                                f"Agency: {safe_field(p, 'agency','')}\n"
                                f"Height: {safe_field(p, 'height','')}\n"
                                f"Waist: {safe_field(p, 'waist','')}\n"
                                f"Dress/Suit: {safe_field(p, 'dress_suit','')}\n"
                                f"Next Available: {safe_field(p, 'availability','')}\n"
                                f"Session: {session_name_map.get(safe_field(p,'session_id',None),'Unassigned')}\n"
                            )
                            row_cells[1].text = info_text
                            doc.add_paragraph("\n")

                        # Save to BytesIO and provide download button
                        word_stream = io.BytesIO()
                        doc.save(word_stream)
                        word_stream.seek(0)
                        st.download_button(
                            label="Click to download Word file",
                            data=word_stream,
                            file_name=f"{current}_participants.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
            except Exception as e:
                st.error(f"Unable to generate Word file: {e}")

        # ------------------------
        # Admin dashboard (unchanged)
        # ------------------------
        if role == "Admin":
            st.header("👑 Admin Dashboard")
            if st.button("🔄 Refresh Users"):
                safe_rerun()

            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")
                users_rows = cur.fetchall()

            ucol1, ucol2 = st.columns([3,2])
            with ucol1:
                uquery = st.text_input("Search accounts by username or role")
            with ucol2:
                urole_filter = st.selectbox("Filter role", ["All", "Admin", "Casting Director", "Assistant"], index=0)

            uhdr = st.columns([3,2,3,3,4])
            uhdr[0].markdown("**Username**"); uhdr[1].markdown("**Role**"); uhdr[2].markdown("**Last Login**"); uhdr[3].markdown("**Projects**"); uhdr[4].markdown("**Actions**")

            for u in users_rows:
                uname = u["username"]
                urole = u["role"]
                last = u["last_login"]
                with db_connect() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM projects WHERE user_id=? ORDER BY name COLLATE NOCASE", (u["id"],))
                    pr = [r["name"] for r in cur.fetchall()]
                projlist = ", ".join(pr)

                if uquery and uquery.lower() not in uname.lower() and uquery.lower() not in (urole or "").lower():
                    continue
                if urole_filter != "All" and urole != urole_filter:
                    continue

                cols = st.columns([3,2,3,3,4])
                cols[0].markdown(f"**{uname}**")
                role_sel = cols[1].selectbox(f"role_sel_{uname}", ["Admin","Casting Director","Assistant"], index=["Admin","Casting Director","Assistant"].index(urole) if urole in ["Admin","Casting Director","Assistant"] else 1, key=f"role_sel_{uname}")
                cols[2].markdown(last or "—")
                cols[3].markdown(projlist or "—")

                a1,a2 = cols[4].columns([1,1])
                if a1.button("Save Role", key=f"saverole_{uname}"):
                    try:
                        with db_transaction() as conn:
                            conn.execute("UPDATE users SET role=? WHERE username=?", (role_sel, uname))
                            log_action(current_username, "change_role", f"{uname} -> {role_sel}")
                        st.success(f"Role updated for {uname}.")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to change role: {e}")

                if a2.button("Delete", key=f"deluser_{uname}"):
                    if uname == "admin":
                        st.error("Cannot delete the built-in admin.")
                    else:
                        try:
                            user_media = os.path.join(MEDIA_DIR, _sanitize_for_path(uname))
                            if os.path.exists(user_media):
                                shutil.rmtree(user_media)
                        except Exception:
                            pass
                        try:
                            with db_transaction() as conn:
                                cur = conn.cursor()
                                cur.execute("SELECT id FROM users WHERE username=?", (uname,))
                                r = cur.fetchone()
                                if r:
                                    uid = r["id"]
                                    cur.execute("SELECT photo_path FROM participants WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                                    for rr in cur.fetchall():
                                        pf = rr["photo_path"]
                                        if isinstance(pf, str) and os.path.exists(pf):
                                            remove_media_file(pf)
                                    cur.execute("DELETE FROM participants WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                                    cur.execute("DELETE FROM projects WHERE user_id=?", (uid,))
                                    cur.execute("DELETE FROM users WHERE id=?", (uid,))
                                    log_action(current_username, "delete_user", uname)
                            st.warning(f"User {uname} deleted.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete user: {e}")
