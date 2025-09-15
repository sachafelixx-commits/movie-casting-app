# sachas_casting_manager_sqlite_sessions_speedup_full.py
# Full app: Sacha's Casting Manager (SQLite) — optimized for speed, thumbnails, sessions, bulk move/copy.
# IMPORTANT: This file is intended to replace your existing Streamlit app file. It preserves previous features
# (projects, participants, admin, Word export) and adds sessions (create/copy/cut bulk move), performance improvements:
#  - cached DB connection (st.cache_resource)
#  - cached image data uris (st.cache_data)
#  - smaller thumbnails created on upload (faster)
#  - reduced unnecessary reruns so UI feedback (success messages) is visible
#
# Tested features included:
#  - Signup/Login (admin backdoor)
#  - Projects per user
#  - Participants per project (photo upload & thumbnail)
#  - Sessions per project; participants have session_id (nullable)
#  - Bulk select participants, copy/cut move to session (multi-select)
#  - Export Word for current session or all participants
#  - Admin dashboard for users
#
# Note: If you're migrating from an older DB missing the participants.session_id column or sessions table,
# the app will attempt to ALTER/CREATE where possible.

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
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image, UnidentifiedImageError
import hashlib
from contextlib import contextmanager

# ========================
# Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager (SQLite)", layout="wide")

DB_FILE = "data.db"

# --- Safety: ensure required DB schema + permanent admin user ---
def _ensure_admin_and_schema():
    """Ensure that the database has the required tables and a permanent admin user.
    This helps keep an admin login (admin / supersecret) available even if the DB file is replaced.
    The routine is conservative: it only creates missing tables and the admin user when absent.
    """
    try:
        import sqlite3 as _sqlite
        _conn = _sqlite.connect(DB_FILE)
        _cur = _conn.cursor()
        try:
            _cur.execute("PRAGMA integrity_check;")
            res = _cur.fetchone()
            if not res or (isinstance(res, tuple) and res[0] != 'ok') or (isinstance(res, str) and res != 'ok'):
                _conn.close()
                return
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            return

        # Ensure users table exists
        _cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not _cur.fetchone():
            _schema = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                last_login TEXT
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
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
                session_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                date TEXT,
                description TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                user TEXT,
                action TEXT,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
            CREATE INDEX IF NOT EXISTS idx_participants_project ON participants(project_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
            """
            _cur.executescript(_schema)
            _conn.commit()

        # Ensure admin user exists
        _cur.execute("SELECT id FROM users WHERE username=?", ("admin",))
        if not _cur.fetchone():
            import hashlib
            from datetime import datetime
            pw = hashlib.sha256("supersecret".encode()).hexdigest()
            now = datetime.now().isoformat()
            _cur.execute(
                "INSERT INTO users (username, password, role, last_login) VALUES (?, ?, ?, ?)",
                ("admin", pw, "Admin", now)
            )
            _conn.commit()

        _conn.close()
    except Exception:
        # don't let safety routine crash the app
        pass

# Run safety routine early
_ensure_admin_and_schema()


USERS_JSON = "users.json"   # used only for migration (optional)
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
DEFAULT_PROJECT_NAME = "Default Project"

# SQLite pragmas (tuning)
PRAGMA_WAL = "WAL"
PRAGMA_SYNCHRONOUS = "NORMAL"

# Thumbnail defaults (smaller for speed)
THUMB_SIZE = (360, 360)
THUMB_QUALITY = 72

# ========================
# Inject UI CSS for letter-box participant cards (touch-friendly)
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
        val = row_or_dict[key]
    except Exception:
        try:
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
        cur.execute("PRAGMA temp_store = MEMORY;")
        # optional: tune cache size if you have memory
        # cur.execute("PRAGMA cache_size = -20000;")
    except Exception:
        pass
    return conn

@contextmanager
def db_transaction():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute(f"PRAGMA synchronous = {PRAGMA_SYNCHRONOUS};")
    except Exception:
        pass
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

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
def save_photo_file(uploaded_file, username: str, project_name: str, make_thumb=True, thumb_size=THUMB_SIZE) -> str:
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
                img.convert("RGB").save(thumb_path, format="JPEG", quality=THUMB_QUALITY)
            except Exception:
                # ignore thumbnail errors
                pass
        # return POSIX-style path
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
            img.thumbnail(THUMB_SIZE)
            thumb_name = f"{os.path.splitext(filename)[0]}_thumb.jpg"
            thumb_path = os.path.join(user_dir, thumb_name)
            img.convert("RGB").save(thumb_path, format="JPEG", quality=THUMB_QUALITY)
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
# SQLite helpers & schema management
# ========================
def db_connect():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute(f"PRAGMA synchronous = {PRAGMA_SYNCHRONOUS};")
    except Exception:
        pass
    return conn

def init_db():
    # create DB and basic schema if missing
    if os.path.exists(DB_FILE):
        # still ensure sessions column/table exist (migration path)
        with db_transaction() as conn:
            cur = conn.cursor()
            # add session_id column if missing
            try:
                cur.execute("PRAGMA table_info(participants);")
                cols = [r[1] for r in cur.fetchall()]
                if "session_id" not in cols:
                    cur.execute("ALTER TABLE participants ADD COLUMN session_id INTEGER;")
            except Exception:
                pass
            # create sessions table if missing
            try:
                cur.execute("""SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'""")
                if not cur.fetchone():
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                            id INTEGER PRIMARY KEY,
                            project_id INTEGER NOT NULL,
                            name TEXT NOT NULL,
                            date TEXT,
                            description TEXT,
                            created_at TEXT
                        );
                    """)
            except Exception:
                pass
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
        c.execute("""
            CREATE TABLE participants (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
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
                session_id INTEGER,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        """)
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
# Migration from users.json (optional)
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
                    projects = {DEFAULT_PROJECT_NAME: {"description":"", "created_at": datetime.now().isoformat(), "participants":[]}}
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
# Session helpers
# ========================
def list_sessions_for_project(conn, project_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY date IS NULL, date, created_at", (project_id,))
    return cur.fetchall()

def create_session(conn, project_id, name, date=None, description=""):
    now = datetime.now().isoformat()
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (project_id, name, date, description, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, name, date, description, now))
    return cur.lastrowid

def get_session(conn, session_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    return cur.fetchone()

def delete_session(conn, session_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE id=?", (session_id,))

def assign_participants_to_session(conn, participant_ids, session_id):
    cur = conn.cursor()
    cur.executemany("UPDATE participants SET session_id=? WHERE id=?", [(session_id, pid) for pid in participant_ids])

def unassign_participants_from_session(conn, participant_ids):
    cur = conn.cursor()
    cur.executemany("UPDATE participants SET session_id=NULL WHERE id=?", [(pid,) for pid in participant_ids])

# ========================
# UI: Auth initial state
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
    st.session_state["bulk_selection"] = set()  # store participant ids for bulk ops
if "view_mode" not in st.session_state:
    st.session_state["view_mode"] = "all"  # "all" or "session" — which participants are currently shown
if "view_session_id" not in st.session_state:
    st.session_state["view_session_id"] = None

# ========================
# AUTH UI: Login / Signup
# ========================
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
        safe_rerun()

    st.sidebar.subheader("Modes")
    try:
        st.session_state["participant_mode"] = st.sidebar.toggle("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))
    except Exception:
        st.session_state["participant_mode"] = st.sidebar.checkbox("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))

    # load user's projects (use cached connection)
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

    # Participant kiosk (simpler, for check-ins)
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
                        (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pid, number, name, role_in, age, agency, height, waist, dress_suit, availability, photo_path))
                    log_action(current_username, "participant_checkin", name)
                st.success("✅ Thanks for checking in!")
                # don't force immediate rerun — allow user to see success
                time.sleep(0.2)
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

        # Create project (show success message, avoid immediate rerun so message visible)
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
                                    # let the rest of the run update views (no immediate rerun)
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
                                        c.execute("DELETE FROM sessions WHERE project_id=?", (pid,))
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
        # Participant + Session management UI
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

        # Sessions panel
        st.subheader("🗓️ Sessions")
        sess_col1, sess_col2 = st.columns([3,2])
        with sess_col1:
            sess_query = st.text_input("Search sessions by name or description", key="sess_query")
        with sess_col2:
            sess_sort = st.selectbox("Sort sessions", ["Date", "Newest", "Oldest", "Name"], index=0, key="sess_sort")

        # create session
        with st.expander("➕ Create New Session", expanded=False):
            with st.form("new_session_form"):
                s_name = st.text_input("Session Name")
                s_date = st.date_input("Date (optional)")
                s_desc = st.text_area("Description", height=80)
                s_create = st.form_submit_button("Create Session")
                if s_create:
                    if not s_name:
                        st.error("Provide a session name")
                    else:
                        try:
                            with db_transaction() as conn:
                                create_session(conn, project_id, s_name, s_date.isoformat() if s_date else None, s_desc or "")
                                log_action(current_username, "create_session", f"{s_name}")
                            st.success(f"Session '{s_name}' created.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to create session: {e}")

        # list sessions
        with db_connect() as conn:
            sessions = list_sessions_for_project(conn, project_id)
        view_sessions = []
        for s in sessions:
            view_sessions.append((s["id"], s["name"], s["date"], s["description"]))

        if sess_query:
            q = sess_query.lower().strip()
            view_sessions = [x for x in view_sessions if q in x[1].lower() or q in (x[3] or "").lower()]

        # sort
        if sess_sort == "Name":
            view_sessions.sort(key=lambda x: (x[1] or "").lower())
        elif sess_sort == "Newest":
            view_sessions.sort(key=lambda x: x[2] or "", reverse=True)
        elif sess_sort == "Oldest":
            view_sessions.sort(key=lambda x: x[2] or "")
        elif sess_sort == "Date":
            # put dated first
            view_sessions.sort(key=lambda x: (x[2] is None, x[2] or ""))

        # render sessions with actions
        sess_hdr = st.columns([4,2,2,2])
        sess_hdr[0].markdown("**Session**")
        sess_hdr[1].markdown("**Date**")
        sess_hdr[2].markdown("**Participants**")
        sess_hdr[3].markdown("**Actions**")

        # precompute participant counts by session
        with db_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT session_id, COUNT(*) as cnt FROM participants WHERE project_id=? GROUP BY session_id", (project_id,))
            counts_map = {r["session_id"]: r["cnt"] for r in cur.fetchall()}

        for sid, sname, sdate, sdesc in view_sessions:
            cnt = counts_map.get(sid, 0)
            cols = st.columns([4,2,2,2])
            cols[0].markdown(f"**{sname}**  \n{sdesc or ''}")
            cols[1].markdown((sdate or "—"))
            cols[2].markdown(str(cnt))
            ca, cb = cols[3].columns([1,1])
            if ca.button("View", key=f"view_sess_{sid}"):
                st.session_state["view_mode"] = "session"
                st.session_state["view_session_id"] = sid
                safe_rerun()
            if cb.button("Delete", key=f"del_sess_{sid}"):
                if st.confirm(f"Delete session '{sname}'? Participants will become unassigned. Continue?"):
                    try:
                        with db_transaction() as conn:
                            # unassign participants
                            cur = conn.cursor()
                            cur.execute("UPDATE participants SET session_id=NULL WHERE session_id=?", (sid,))
                            delete_session(conn, sid)
                            log_action(current_username, "delete_session", f"{sname}")
                        st.success("Session deleted.")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to delete session: {e}")

        # option to view all participants
        view_all_btn = st.button("View All Participants", key="view_all_btn")
        if view_all_btn:
            st.session_state["view_mode"] = "all"
            st.session_state["view_session_id"] = None
            safe_rerun()

        # Participant management UI
        st.header(f"👥 Participants — {current}")

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
                p_session = None
                # allow quick assign to a session on create
                sess_options = [("Unassigned", None)] + [(s["name"], s["id"]) for s in sessions]
                # display as selectbox
                sess_choice = st.selectbox("Assign to session (optional)", options=[x[0] for x in sess_options])
                for n,v in sess_options:
                    if n == sess_choice:
                        p_session = v
                        break
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                submitted = st.form_submit_button("Add Participant")
                if submitted:
                    try:
                        with db_transaction() as conn:
                            photo_path = save_photo_file(photo, current_username, current) if photo else None
                            conn.execute("""
                                INSERT INTO participants
                                (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path, session_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, number, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, photo_path, p_session))
                            log_action(current_username, "add_participant", pname)
                        st.success("Participant added!")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to add participant: {e}")

        # list participants filtered by view_mode
        if st.session_state.get("view_mode") == "session" and st.session_state.get("view_session_id"):
            cur_view_session = st.session_state["view_session_id"]
            header_text = f"Participants — Session view"
            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY id", (project_id, cur_view_session))
                participants = cur.fetchall()
        else:
            # view all
            header_text = "Participants — All"
            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                participants = cur.fetchall()

        st.subheader(header_text)

        if not participants:
            st.info("No participants yet.")
        else:
            # Bulk controls
            st.markdown("**Bulk actions:**")
            bulk_cols = st.columns([1,1,1,2])
            bulk_select_action = bulk_cols[0].selectbox("Select action", ["Select", "Deselect", "Invert"], index=0, key="bulk_action_select")
            if bulk_cols[1].button("Apply selection"):
                # apply selection action to visible participants
                ids = [p["id"] for p in participants]
                if bulk_select_action == "Select":
                    st.session_state["bulk_selection"].update(ids)
                elif bulk_select_action == "Deselect":
                    st.session_state["bulk_selection"].difference_update(ids)
                else:  # Invert
                    new_sel = set(ids).difference(st.session_state["bulk_selection"])
                    st.session_state["bulk_selection"].difference_update(ids)
                    st.session_state["bulk_selection"].update(new_sel)
                st.success("Selection updated.")
                safe_rerun()
            bulk_target_session = None
            sess_map = {None: "Unassigned"}
            for s in sessions:
                sess_map[s["id"]] = s["name"]
            # choices display
            sess_choice_bulk = bulk_cols[2].selectbox("Move selected to session (or Unassigned)", options=list(sess_map.values()))
            # resolve target id
            for k,v in sess_map.items():
                if v == sess_choice_bulk:
                    bulk_target_session = k
                    break
            copy_or_cut = bulk_cols[3].selectbox("Copy or Move?", ["Move (cut)", "Copy"], index=0)

            # Show bulk move/copy button
            if st.button("Apply bulk action (Move/Copy)"):
                sel_ids = list(st.session_state["bulk_selection"])
                if not sel_ids:
                    st.warning("No participants selected.")
                else:
                    try:
                        with db_transaction() as conn:
                            # if copy: duplicate participant rows with new session_id
                            cur = conn.cursor()
                            if copy_or_cut == "Copy":
                                for pid in sel_ids:
                                    cur.execute("SELECT * FROM participants WHERE id=?", (pid,))
                                    row = cur.fetchone()
                                    if not row:
                                        continue
                                    cur.execute("""INSERT INTO participants
                                        (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path, session_id)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                        (project_id, row["number"], row["name"], row["role"], row["age"], row["agency"], row["height"], row["waist"], row["dress_suit"], row["availability"], row["photo_path"], bulk_target_session))
                            else:
                                # Move (update session_id)
                                cur.executemany("UPDATE participants SET session_id=? WHERE id=?", [(bulk_target_session, pid) for pid in sel_ids])
                            log_action(current_username, "bulk_move_copy", f"{len(sel_ids)} items -> session {bulk_target_session}")
                        st.success("Bulk action applied.")
                        # clear selection
                        st.session_state["bulk_selection"].clear()
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Bulk action failed: {e}")

            # Render participants in letterbox cards with right-side actions
            for p in participants:
                pid = p["id"]
                left, right = st.columns([9,1])
                display_path = thumb_path_for(safe_field(p, "photo_path", ""))
                data_uri = image_b64_for_path(display_path) if display_path else None
                if data_uri:
                    img_tag = f"<img class='photo' src='{data_uri}' alt='photo'/>"
                else:
                    img_tag = "<div class='photo' style='display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"

                name_html = safe_field(p, "name", "Unnamed")
                number_html = safe_field(p, "number", "")
                role_html = safe_field(p, "role", "")
                age_html = safe_field(p, "age", "")
                agency_html = safe_field(p, "agency", "")
                height_html = safe_field(p, "height", "")
                waist_html = safe_field(p, "waist", "")
                dress_html = safe_field(p, "dress_suit", "")
                avail_html = safe_field(p, "availability", "")
                session_id_html = safe_field(p, "session_id", None)
                session_name_html = "Unassigned"
                for s in sessions:
                    if s["id"] == session_id_html:
                        session_name_html = s["name"]
                        break

                card_html = f"""
                    <div class="participant-letterbox">
                        {img_tag}
                        <div class="name">{st.markdown(name_html + (f" <span class='small'>#{number_html}</span>" if number_html else ''), unsafe_allow_html=True)}</div>
                    </div>
                """
                # We want to show the full card with details — but to avoid duplicate rendering complexity, render with markdown below
                # Using left.markdown with the constructed HTML:
                left.markdown(f"""
                    <div class="participant-letterbox">
                        {img_tag}
                        <div class="name">{name_html} <span class="small">#{number_html}</span></div>
                        <div class="meta">Role: {role_html} • Age: {age_html}</div>
                        <div class="meta">Agency: {agency_html}</div>
                        <div class="meta">Height: {height_html} • Waist: {waist_html} • Dress/Suit: {dress_html}</div>
                        <div class="small">Availability: {avail_html}</div>
                        <div class="small" style="margin-top:6px;"><strong>Session:</strong> {session_name_html}</div>
                    </div>
                """, unsafe_allow_html=True)

                # Bulk checkbox control (in right column above Edit/Delete)
                is_selected = pid in st.session_state["bulk_selection"]
                # We'll use a checkbox to toggle selection
                sel = right.checkbox("Select", value=is_selected, key=f"bulkchk_{pid}")
                if sel and pid not in st.session_state["bulk_selection"]:
                    st.session_state["bulk_selection"].add(pid)
                if not sel and pid in st.session_state["bulk_selection"]:
                    st.session_state["bulk_selection"].discard(pid)

                # Edit/Delete underneath checkbox
                if right.button("Edit", key=f"edit_{pid}"):
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
                        # session assignment
                        sess_assign_opts = ["Unassigned"] + [s["name"] for s in sessions]
                        cur_sess_name = session_name_html
                        e_session_choice = st.selectbox("Assign to session", options=sess_assign_opts, index=sess_assign_opts.index(cur_sess_name) if cur_sess_name in sess_assign_opts else 0)
                        ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                        save_edit = st.form_submit_button("Save Changes")
                        cancel_edit = st.form_submit_button("Cancel")
                        if save_edit:
                            try:
                                with db_transaction() as conn:
                                    new_photo_path = safe_field(p, "photo_path", None)
                                    if ephoto:
                                        new_photo_path = save_photo_file(ephoto, current_username, current)
                                        oldphoto = safe_field(p, "photo_path", None)
                                        if isinstance(oldphoto, str) and os.path.exists(oldphoto):
                                            remove_media_file(oldphoto)
                                    # resolve session id
                                    new_session_id = None
                                    for s in sessions:
                                        if s["name"] == e_session_choice:
                                            new_session_id = s["id"]
                                            break
                                    if e_session_choice == "Unassigned":
                                        new_session_id = None
                                    conn.execute("""
                                        UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?, session_id=?
                                        WHERE id=?
                                    """, (enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, new_session_id, pid))
                                    log_action(current_username, "edit_participant", ename)
                                st.success("Participant updated!")
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to save participant edits: {e}")
                        if cancel_edit:
                            safe_rerun()

                if right.button("Delete", key=f"del_{pid}"):
                    try:
                        with db_transaction() as conn:
                            if isinstance(safe_field(p, "photo_path", ""), str) and os.path.exists(safe_field(p, "photo_path", "")):
                                remove_media_file(safe_field(p, "photo_path", ""))
                            conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                            log_action(current_username, "delete_participant", safe_field(p, "name", ""))
                        st.warning("Participant deleted")
                        # ensure it's removed from bulk selection if present
                        st.session_state["bulk_selection"].discard(pid)
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to delete participant: {e}")

        # ------------------------
        # Export to Word (current view or all)
        # ------------------------
        st.subheader("📄 Export Participants (Word)")
        st.write("Choose export scope:")
        col_e = st.columns([1,1,1])
        export_scope = col_e[0].selectbox("Export:", ["Export current view (session/all)", "Export all participants"])
        if col_e[1].button("Download Word File of Current View"):
            try:
                with db_connect() as conn:
                    cur = conn.cursor()
                    if st.session_state.get("view_mode") == "session" and st.session_state.get("view_session_id"):
                        cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY id", (project_id, st.session_state.get("view_session_id")))
                        parts = cur.fetchall()
                        doc_title = f"{current}_session_{st.session_state.get('view_session_id')}_participants"
                    else:
                        cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                        parts = cur.fetchall()
                        doc_title = f"{current}_participants"
                    if not parts:
                        st.info("No participants in this selection.")
                    else:
                        doc = Document()
                        doc.add_heading(f"Participants - {current}", 0)
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
                                f"Session: {session_name_html if 'session_name_html' in locals() else (safe_field(p,'session_id') or 'Unassigned')}"
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
                            file_name=f"{doc_title}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
            except Exception as e:
                st.error(f"Unable to generate Word file: {e}")

        # ------------------------
        # Admin dashboard (unchanged behavior)
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
                uquery = st.text_input("Search accounts by username or role", key="admin_uquery")
            with ucol2:
                urole_filter = st.selectbox("Filter role", ["All", "Admin", "Casting Director", "Assistant"], index=0, key="admin_role_filter")

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
                                    cur.execute("DELETE FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                                    cur.execute("DELETE FROM projects WHERE user_id=?", (uid,))
                                    cur.execute("DELETE FROM users WHERE id=?", (uid,))
                                    log_action(current_username, "delete_user", uname)
                            st.warning(f"User {uname} deleted.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete user: {e}")






