# sachas_casting_manager_sqlite_sessions_separated.py
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
USERS_JSON = "users.json"   # used only for migration
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
DEFAULT_PROJECT_NAME = "Default Project"

# SQLite pragmas
PRAGMA_WAL = "WAL"
PRAGMA_SYNCHRONOUS = "NORMAL"

# ========================
# Inject UI CSS for letter-box participant cards + grid + toolbar
# ========================
st.markdown("""
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
/* Toolbar */
.toolbar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.toolbar .stButton>button { padding: .7rem 1rem !important; font-size: 1rem !important; border-radius: 0.6rem; }

/* Participant letter-box card */
.participant-letterbox {
  max-width: 520px;
  border-radius: 10px;
  border: 1px solid rgba(0,0,0,0.06);
  padding: 10px;
  margin-bottom: 12px;
  background: #ffffff;
  box-shadow: 0 2px 10px rgba(0,0,0,0.04);
  color: #000; 
  position: relative;
}
.participant-letterbox .photo {
  width: 100%;
  height: 220px;
  display:block;
  object-fit: cover;
  border-radius: 8px;
  background: #f6f6f6;
  margin-bottom: 8px;
  border: 1px solid rgba(0,0,0,0.04);
}
.participant-letterbox .name {
  font-weight: 800;
  font-size: 1.12rem;
  margin-bottom: 6px;
  color: #000 !important;
  line-height: 1.15;
  letter-spacing: 0.2px;
}
.participant-letterbox .name .small {
  color: rgba(0,0,0,0.45) !important;
  font-weight: 600;
  font-size: 0.95rem;
  margin-left: 6px;
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

/* Checkbox overlay for bulk select (when enabled) */
.participant-letterbox .bulk-check {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 20;
  background: rgba(255,255,255,0.8);
  padding: 4px;
  border-radius: 6px;
}

/* Grid card */
.grid-card {
  border-radius:8px;
  padding:8px;
  background:#fff;
  box-shadow:0 1px 6px rgba(0,0,0,0.04);
  text-align:center;
  margin-bottom:10px;
  position:relative;
}
.grid-card .thumb { height:150px; object-fit:cover; border-radius:6px; width:100%; display:block; margin-bottom:6px; }
.grid-card .name { font-weight:700; font-size:1rem; color:#000; margin-bottom:6px; }
.grid-card .meta { color:rgba(0,0,0,0.6); font-size:0.9rem; }

/* Sessions list */
.sessions-list { display:flex; flex-direction:column; gap:8px; }
.session-row { display:flex; gap:8px; align-items:center; padding:8px; border-radius:8px; background:#fff; border:1px solid rgba(0,0,0,0.03); }
.session-row .meta { color:rgba(0,0,0,0.6); font-size:0.9rem; }
.session-row .actions { margin-left:auto; display:flex; gap:6px; }

/* Responsive */
@media (max-width: 900px) {
  .participant-letterbox .photo { height: 160px; }
  .grid-card .thumb { height:120px; }
}
@media (max-width: 600px) {
  .participant-letterbox { max-width: 100%; padding: 8px; }
  .participant-letterbox .photo { height: 140px; }
  .part-row { flex-direction: column; }
}

/* Slight protection against weird theme color inheritance */
.stMarkdown p, .stMarkdown div { color: inherit !important; }
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
    if not photo_field:
        return None
    if isinstance(photo_field, str) and os.path.exists(photo_field):
        try:
            with open(photo_field, "rb") as f:
                return f.read()
        except Exception:
            return None
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
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
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
        c.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                date TEXT,
                created_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        """)
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
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
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

def ensure_schema_upgrades():
    if not os.path.exists(DB_FILE):
        return
    try:
        with db_transaction() as conn:
            cur = conn.cursor()
            try:
                cur.execute("PRAGMA table_info(sessions);")
                rows = cur.fetchall()
                cols = [r["name"] for r in rows] if rows else []
                if "date" not in cols:
                    try:
                        cur.execute("ALTER TABLE sessions ADD COLUMN date TEXT;")
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                cur.execute("PRAGMA table_info(participants);")
                rows = cur.fetchall()
                cols = [r["name"] for r in rows] if rows else []
                if "session_id" not in cols:
                    try:
                        cur.execute("ALTER TABLE participants ADD COLUMN session_id INTEGER;")
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);")
            except Exception:
                pass
    except Exception:
        pass

# ------------------------
# log_action
# ------------------------
def log_action(user, action, details=""):
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
                                (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                project_id,
                                None,
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
ensure_schema_upgrades()
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

# Session helpers
def list_sessions_for_project(conn, project_id):
    c = conn.cursor()
    c.execute("""SELECT s.*, COALESCE(cnt.cnt,0) AS participant_count
                 FROM sessions s
                 LEFT JOIN (SELECT session_id, COUNT(*) as cnt FROM participants GROUP BY session_id) cnt
                 ON cnt.session_id = s.id
                 WHERE s.project_id=?
                 ORDER BY s.created_at""", (project_id,))
    return c.fetchall()

def create_session(conn, project_id, name, date=None):
    now = datetime.now().isoformat()
    c = conn.cursor()
    c.execute("INSERT INTO sessions (project_id, name, date, created_at) VALUES (?, ?, ?, ?)",
              (project_id, name, date, now))
    return c.lastrowid

def get_session_by_name(conn, project_id, name):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE project_id=? AND name=?", (project_id, name))
    return c.fetchone()

def rename_session(conn, session_id, new_name, new_date=None):
    c = conn.cursor()
    c.execute("UPDATE sessions SET name=?, date=? WHERE id=?", (new_name, new_date, session_id))

def delete_session_and_unassign(conn, session_id):
    """Delete session and set session_id NULL on associated participants."""
    c = conn.cursor()
    c.execute("UPDATE participants SET session_id=NULL WHERE session_id=?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

# Duplicate participant row helper (used for copying)
def duplicate_participant_row(conn, prow, target_session_id, username, project_name):
    try:
        src = prow["photo_path"]
        new_path = None
        if isinstance(src, str) and os.path.exists(src):
            with open(src, "rb") as f:
                data = f.read()
            new_path = save_photo_bytes(data, username, project_name)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO participants
            (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            prow["project_id"],
            target_session_id,
            prow["number"],
            prow["name"],
            prow["role"],
            prow["age"],
            prow["agency"],
            prow["height"],
            prow["waist"],
            prow["dress_suit"],
            prow["availability"],
            new_path
        ))
        return cur.lastrowid
    except Exception:
        return None

# ========================
# UI: Auth and state init
# ========================
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "current_project_name" not in st.session_state:
    st.session_state["current_project_name"] = None
if "current_session_filter" not in st.session_state:
    st.session_state["current_session_filter"] = "All"  # "All" or session_id (int)
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
if "open_new_project" not in st.session_state:
    st.session_state["open_new_project"] = False
if "open_add_participant" not in st.session_state:
    st.session_state["open_add_participant"] = False
if "open_new_session" not in st.session_state:
    st.session_state["open_new_session"] = False
if "open_bulk_actions" not in st.session_state:
    st.session_state["open_bulk_actions"] = False
if "bulk_mode" not in st.session_state:
    st.session_state["bulk_mode"] = False
if "view_mode" not in st.session_state:
    st.session_state["view_mode"] = "Letterbox"
if "participants_offset" not in st.session_state:
    st.session_state["participants_offset"] = 0
if "editing_participant" not in st.session_state:
    st.session_state["editing_participant"] = None

if not st.session_state["logged_in"]:
    st.title("🎬 Sacha's Casting Manager")
    choice = st.radio("Choose an option", ["Login", "Sign Up"], horizontal=True)

    if choice == "Login":
        username = st.text_input("Username", value=st.session_state.get("prefill_username", ""))
        if st.session_state.get("prefill_username"):
            st.session_state["prefill_username"] = ""
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")
        if login_btn:
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
                            st.session_state["prefill_username"] = new_user
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
        st.session_state["current_session_filter"] = "All"
        safe_rerun()

    st.sidebar.subheader("Modes")
    try:
        st.session_state["participant_mode"] = st.sidebar.toggle("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))
    except Exception:
        st.session_state["participant_mode"] = st.sidebar.checkbox("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))

    # load user's projects
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
                try:
                    image_b64_for_path.clear()
                except Exception:
                    pass
                st.session_state["participants_offset"] = 0
                st.success("✅ Thanks for checking in!")
                safe_rerun()

    # Casting manager mode
    else:
        st.title("🎬 Sacha's Casting Manager")

        # Toolbar
        st.markdown("<div class='toolbar'></div>", unsafe_allow_html=True)
        tcols = st.columns([1,1,1,1,1])
        if tcols[0].button("➕ New Project"):
            st.session_state["open_new_project"] = True
            safe_rerun()
        if tcols[1].button("➕ New Participant"):
            st.session_state["open_add_participant"] = True
            safe_rerun()
        if tcols[2].button("📅 New Session"):
            st.session_state["open_new_session"] = True
            safe_rerun()
        if tcols[3].button("🔀 Bulk Actions"):
            st.session_state["open_bulk_actions"] = True
            st.session_state["bulk_mode"] = True
            safe_rerun()
        if tcols[4].button("📄 Export"):
            st.session_state["open_export"] = True
            safe_rerun()

        # Project Manager UI
        st.header("📁 Project Manager")
        pm_col1, pm_col2 = st.columns([3,2])
        with pm_col1:
            query = st.text_input("Search projects by name or description")
        with pm_col2:
            sort_opt = st.selectbox("Sort by", ["Name A→Z", "Newest", "Oldest", "Most Participants", "Fewest Participants"], index=0)

        # Create project
        with st.expander("➕ Create New Project", expanded=st.session_state.get("open_new_project", False)):
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
                                    st.session_state["current_project_name"] = p_name
                                    st.session_state["open_new_project"] = False
                        except Exception as e:
                            st.error(f"Unable to create project: {e}")

        # fetch fresh projects
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
                st.session_state["current_session_filter"] = "All"
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

        # Participant management UI
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

        # ------------------------
        # Sessions panel (separate)
        # ------------------------
        st.subheader("📅 Sessions (separate panel)")
        sess_col_left, sess_col_right = st.columns([3,1])

        # Left: sessions list with counts
        with db_connect() as conn:
            sessions_all = list_sessions_for_project(conn, project_id)

        with sess_col_left:
            st.markdown("<div class='sessions-list'>", unsafe_allow_html=True)
            # "All participants" option
            all_selected = st.session_state.get("current_session_filter") == "All"
            if st.button("View: All participants"):
                st.session_state["current_session_filter"] = "All"
                safe_rerun()
            for s in sessions_all:
                sid = s["id"]
                sname = s["name"]
                sdate = s["date"] or ""
                scount = safe_field(s, "participant_count", 0)
                row_html = f"""
                    <div class='session-row'>
                        <div class='meta'><strong>{sname}</strong><div class='meta'>{sdate or ''}</div></div>
                        <div class='meta'>Participants: {scount}</div>
                        <div class='actions'></div>
                    </div>
                """
                st.markdown(row_html, unsafe_allow_html=True)
                # Buttons for each session:
                c1, c2, c3 = st.columns([1,1,1])
                if c1.button("View", key=f"view_sess_{sid}"):
                    st.session_state["current_session_filter"] = sid
                    safe_rerun()
                if c2.button("Edit", key=f"edit_sess_{sid}"):
                    st.session_state["editing_session"] = sid
                    safe_rerun()
                if c3.button("Delete", key=f"del_sess_{sid}"):
                    st.session_state["confirm_delete_session"] = sid
                    safe_rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        # Right: session creation + editing
        with sess_col_right:
            st.markdown("**Create session**")
            new_sess_name = st.text_input("Name", key="new_session_name_short")
            new_sess_date = st.text_input("Date (optional)", key="new_session_date_short")
            if st.button("Create session (panel)"):
                if not new_sess_name:
                    st.error("Provide a session name")
                else:
                    try:
                        with db_transaction() as conn:
                            create_session(conn, project_id, new_sess_name, new_sess_date or None)
                            log_action(current_username, "create_session", new_sess_name)
                        st.success("Session created.")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to create session: {e}")

            # Edit session form (if set)
            if st.session_state.get("editing_session"):
                sid = st.session_state.get("editing_session")
                with db_connect() as conn:
                    c = conn.cursor()
                    c.execute("SELECT * FROM sessions WHERE id=? AND project_id=?", (sid, project_id))
                    srow = c.fetchone()
                if srow:
                    st.markdown("**Edit session**")
                    ename = st.text_input("Name", value=srow["name"], key=f"esen_{sid}")
                    edate = st.text_input("Date", value=srow["date"] or "", key=f"esd_{sid}")
                    if st.button("Save session", key=f"save_sess_{sid}"):
                        try:
                            with db_transaction() as conn:
                                rename_session(conn, sid, ename, edate or None)
                                log_action(current_username, "edit_session", f"{sid} -> {ename}")
                            st.success("Session updated.")
                            st.session_state["editing_session"] = None
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to edit session: {e}")
                    if st.button("Cancel edit", key=f"cancel_sess_{sid}"):
                        st.session_state["editing_session"] = None
                        safe_rerun()

            # Confirm delete session
            if st.session_state.get("confirm_delete_session"):
                sid = st.session_state.get("confirm_delete_session")
                with db_connect() as conn:
                    c = conn.cursor()
                    c.execute("SELECT * FROM sessions WHERE id=? AND project_id=?", (sid, project_id))
                    srow = c.fetchone()
                if srow:
                    st.warning(f"Delete session **{srow['name']}**? This will unassign its participants.")
                    if st.button("Delete session permanently", key=f"do_del_sess_{sid}"):
                        try:
                            with db_transaction() as conn:
                                delete_session_and_unassign(conn, sid)
                                log_action(current_username, "delete_session", srow["name"])
                            st.success("Session deleted and participants unassigned.")
                            st.session_state["confirm_delete_session"] = None
                            # if the deleted session was currently filtering view, reset to All
                            if st.session_state.get("current_session_filter") == sid:
                                st.session_state["current_session_filter"] = "All"
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete session: {e}")
                    if st.button("Cancel", key=f"cancel_del_sess_{sid}"):
                        st.session_state["confirm_delete_session"] = None
                        safe_rerun()

        # ------------------------
        # View mode toggle (Letterbox / Grid)
        # ------------------------
        st.markdown("**View participants as:**")
        st.session_state["view_mode"] = st.radio("View mode", ["Letterbox", "Grid"], index=0 if st.session_state.get("view_mode","Letterbox")=="Letterbox" else 1, horizontal=True)

        # Bulk actions expander
        with st.expander("🔀 Bulk actions (move/copy participants)", expanded=st.session_state.get("open_bulk_actions", False)):
            bulk_toggle = st.checkbox("Bulk selection mode (show checkboxes on participant cards)", value=st.session_state.get("bulk_mode", False))
            st.session_state["bulk_mode"] = bulk_toggle

            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name, number FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                all_parts = cur.fetchall()

            st.markdown("**Select participants**")
            sel_cols = st.columns([1,1,2])
            if sel_cols[0].button("Select all"):
                for r in all_parts:
                    key = f"bulk_sel_{r['id']}"
                    st.session_state[key] = True
                safe_rerun()
            if sel_cols[1].button("Clear selection"):
                for r in all_parts:
                    key = f"bulk_sel_{r['id']}"
                    st.session_state[key] = False
                safe_rerun()

            for r in all_parts:
                key = f"bulk_sel_{r['id']}"
                checked = st.session_state.get(key, False)
                st.checkbox(f"{r['id']} | {r['name'] or 'Unnamed'}", value=checked, key=key)

            # target sessions listing including Unassigned
            with db_connect() as conn:
                sess_for_bulk = list_sessions_for_project(conn, project_id)
            session_choices_for_ui = [("Unassigned", None)] + [(s["name"], s["id"]) for s in sess_for_bulk]
            session_labels = [c[0] for c in session_choices_for_ui]
            target_label = st.selectbox("Target session", session_labels, index=0, key="bulk_target_session_panel")
            target_idx = session_labels.index(target_label)
            target_session_id = session_choices_for_ui[target_idx][1]
            action_choice = st.radio("Action", ["Move (cut)","Copy"], index=0, horizontal=True)
            if st.button("Apply bulk action"):
                ids = []
                for r in all_parts:
                    key = f"bulk_sel_{r['id']}"
                    if st.session_state.get(key):
                        ids.append(r['id'])
                if not ids:
                    st.error("Please select at least one participant to proceed.")
                else:
                    try:
                        with db_transaction() as conn:
                            if action_choice.startswith("Move"):
                                tgt = target_session_id
                                q = "UPDATE participants SET session_id=? WHERE id=?"
                                for pid in ids:
                                    conn.execute(q, (tgt, pid))
                                log_action(current_username, "bulk_move", json.dumps({"ids":ids,"target":target_session_id}))
                                st.success(f"Moved {len(ids)} participant(s).")
                                try:
                                    image_b64_for_path.clear()
                                except Exception:
                                    pass
                                st.session_state["participants_offset"] = 0
                            else:
                                copied = 0
                                for pid in ids:
                                    cur = conn.cursor()
                                    cur.execute("SELECT * FROM participants WHERE id=?", (pid,))
                                    prow = cur.fetchone()
                                    if prow:
                                        new_id = duplicate_participant_row(conn, prow, target_session_id, current_username, active)
                                        if new_id:
                                            copied += 1
                                log_action(current_username, "bulk_copy", json.dumps({"ids":ids,"target":target_session_id,"copied":copied}))
                                st.success(f"Copied {copied} participant(s).")
                                try:
                                    image_b64_for_path.clear()
                                except Exception:
                                    pass
                                st.session_state["participants_offset"] = 0
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Bulk action failed: {e}")

            if st.button("Exit bulk mode"):
                for r in all_parts:
                    key = f"bulk_sel_{r['id']}"
                    if key in st.session_state:
                        del st.session_state[key]
                st.session_state["bulk_mode"] = False
                st.session_state["open_bulk_actions"] = False
                safe_rerun()

        # --- Add new participant form ---
        with st.expander("➕ Add New Participant", expanded=st.session_state.get("open_add_participant", False)):
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
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                with db_connect() as conn:
                    all_sessions = list_sessions_for_project(conn, project_id)
                sess_choices = [("Unassigned", None)] + [(s["name"], s["id"]) for s in all_sessions]
                sess_labels = [c[0] for c in sess_choices]
                sess_sel = st.selectbox("Assign to session (optional)", sess_labels, index=0, key="add_assign_session")
                submitted = st.form_submit_button("Add Participant")
                if submitted:
                    try:
                        assign_id = None
                        if sess_sel != "Unassigned":
                            assign_id = next((c[1] for c in sess_choices if c[0]==sess_sel), None)
                        with db_transaction() as conn:
                            photo_path = save_photo_file(photo, current_username, current) if photo else None
                            conn.execute("""
                                INSERT INTO participants
                                (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, assign_id, number, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, photo_path))
                            log_action(current_username, "add_participant", pname)
                        try:
                            image_b64_for_path.clear()
                        except Exception:
                            pass
                        st.session_state["participants_offset"] = 0
                        st.session_state["open_add_participant"] = False
                        st.success("Participant added!")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to add participant: {e}")

        # list participants (paginated) filtered by session selection
        PAGE_SIZE = 12
        offset = st.session_state.get("participants_offset", 0)
        current_filter = st.session_state.get("current_session_filter", "All")

        with db_connect() as conn:
            cur = conn.cursor()
            if current_filter == "All":
                cur.execute("SELECT COUNT(*) as cnt FROM participants WHERE project_id=?", (project_id,))
                total = cur.fetchone()["cnt"]
                cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id LIMIT ? OFFSET ?", (project_id, PAGE_SIZE, offset))
                participants = cur.fetchall()
            else:
                cur.execute("SELECT COUNT(*) as cnt FROM participants WHERE project_id=? AND session_id=?", (project_id, current_filter))
                total = cur.fetchone()["cnt"]
                cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY id LIMIT ? OFFSET ?", (project_id, current_filter, PAGE_SIZE, offset))
                participants = cur.fetchall()

        if not participants:
            if total == 0:
                st.info("No participants yet for this selection.")
            else:
                st.info("No more participants to show on this page.")
        else:
            with db_connect() as conn:
                sess_rows = list_sessions_for_project(conn, project_id)
            sess_map = {s["id"]: s["name"] for s in sess_rows}

            if st.session_state.get("view_mode","Letterbox") == "Letterbox":
                for p in participants:
                    pid = p["id"]
                    left, right = st.columns([9,1])
                    display_path = thumb_path_for(p["photo_path"])
                    data_uri = image_b64_for_path(display_path) if display_path else None
                    if data_uri:
                        img_tag = f"<img class='photo' src='{data_uri}' alt='photo'/>"
                    else:
                        img_tag = "<div class='photo' style='display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"
                    sess_label = sess_map.get(p["session_id"], "Unassigned")

                    name_html = safe_field(p, "name", "Unnamed")
                    number_html = safe_field(p, "number", "")
                    role_html = safe_field(p, "role", "")
                    age_html = safe_field(p, "age", "")
                    agency_html = safe_field(p, "agency", "")
                    height_html = safe_field(p, "height", "")
                    waist_html = safe_field(p, "waist", "")
                    dress_html = safe_field(p, "dress_suit", "")
                    avail_html = safe_field(p, "availability", "")

                    bulk_html = ""
                    if st.session_state.get("bulk_mode", False):
                        key = f"bulk_sel_{pid}"
                        if key not in st.session_state:
                            st.session_state[key] = False
                        bulk_html = f"<div class='bulk-check'>{'☑' if st.session_state.get(key) else '☐'}</div>"

                    card_html = f"""
                        <div class="participant-letterbox">
                            {bulk_html}
                            {img_tag}
                            <div class="name">{name_html}<span class="small">#{number_html}</span></div>
                            <div class="meta">Role: {role_html} • Age: {age_html}</div>
                            <div class="meta">Agency: {agency_html}</div>
                            <div class="meta">Height: {height_html} • Waist: {waist_html} • Dress/Suit: {dress_html}</div>
                            <div class="small">Availability: {avail_html}</div>
                            <div class="small" style="margin-top:6px;"><strong>Session:</strong> {sess_label}</div>
                        </div>
                    """
                    left.markdown(card_html, unsafe_allow_html=True)

                    if right.button("Edit", key=f"edit_{pid}"):
                        st.session_state["editing_participant"] = pid
                        safe_rerun()

                    if right.button("Delete", key=f"del_{pid}"):
                        try:
                            with db_transaction() as conn:
                                if isinstance(p["photo_path"], str) and os.path.exists(p["photo_path"]):
                                    remove_media_file(p["photo_path"])
                                conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                                log_action(current_username, "delete_participant", p["name"] or "")
                                if st.session_state.get("editing_participant") == pid:
                                    st.session_state["editing_participant"] = None
                            try:
                                image_b64_for_path.clear()
                            except Exception:
                                pass
                            st.session_state["participants_offset"] = 0
                            st.warning("Participant deleted")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete participant: {e}")

                    if st.session_state.get("editing_participant") == pid:
                        with st.form(f"edit_participant_form_{pid}"):
                            enumber = st.text_input("Number", value=safe_field(p, "number", ""), key=f"enumber_{pid}")
                            ename = st.text_input("Name", value=safe_field(p, "name", ""), key=f"ename_{pid}")
                            erole = st.text_input("Role", value=safe_field(p, "role", ""), key=f"erole_{pid}")
                            eage = st.text_input("Age", value=safe_field(p, "age", ""), key=f"eage_{pid}")
                            eagency = st.text_input("Agency", value=safe_field(p, "agency", ""), key=f"eagency_{pid}")
                            eheight = st.text_input("Height", value=safe_field(p, "height", ""), key=f"eheight_{pid}")
                            ewaist = st.text_input("Waist", value=safe_field(p, "waist", ""), key=f"ewaist_{pid}")
                            edress = st.text_input("Dress/Suit", value=safe_field(p, "dress_suit", ""), key=f"edress_{pid}")
                            eavail = st.text_input("Next Availability", value=safe_field(p, "availability", ""), key=f'eavail_{pid}')
                            ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"], key=f"ephoto_{pid}")
                            with db_connect() as conn:
                                sess_rows = list_sessions_for_project(conn, project_id)
                            assign_choices = [("Unassigned", None)] + [(s["name"], s["id"]) for s in sess_rows]
                            assign_labels = [c[0] for c in assign_choices]
                            default_idx = 0
                            if p["session_id"] is not None:
                                for i, c in enumerate(assign_choices):
                                    if c[1] == p["session_id"]:
                                        default_idx = i
                                        break
                            sel = st.selectbox("Assign session", assign_labels, index=default_idx, key=f"assign_sel_{pid}")
                            save_edit = st.form_submit_button("Save Changes")
                            cancel_edit = st.form_submit_button("Cancel")
                            if save_edit:
                                try:
                                    with db_transaction() as conn:
                                        new_photo_path = p["photo_path"]
                                        if ephoto:
                                            new_photo_path = save_photo_file(ephoto, current_username, current)
                                            oldphoto = p["photo_path"]
                                            if isinstance(oldphoto, str) and os.path.exists(oldphoto):
                                                remove_media_file(oldphoto)
                                        target_sid = None
                                        if sel != "Unassigned":
                                            target_sid = next((c[1] for c in assign_choices if c[0] == sel), None)
                                        conn.execute("""
                                            UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?, session_id=?
                                            WHERE id=?
                                        """, (enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, target_sid, pid))
                                        log_action(current_username, "edit_participant", ename)
                                    try:
                                        image_b64_for_path.clear()
                                    except Exception:
                                        pass
                                    st.session_state["participants_offset"] = 0
                                    st.success("Participant updated!")
                                    st.session_state["editing_participant"] = None
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Unable to save participant edits: {e}")
                            if cancel_edit:
                                st.session_state["editing_participant"] = None
                                safe_rerun()

            else:
                cols_count = 3
                cols = st.columns(cols_count)
                for i, p in enumerate(participants):
                    c = cols[i % cols_count]
                    pid = p["id"]
                    display_path = thumb_path_for(p["photo_path"])
                    data_uri = image_b64_for_path(display_path) if display_path else None
                    if data_uri:
                        thumb_tag = f"<img class='thumb' src='{data_uri}' alt='photo'/>"
                    else:
                        thumb_tag = "<div class='thumb' style='display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"
                    name_html = safe_field(p, "name", "Unnamed")
                    number_html = safe_field(p, "number", "")
                    role_html = safe_field(p, "role", "")
                    sess_label = sess_map.get(p["session_id"], "Unassigned")
                    if st.session_state.get("bulk_mode", False):
                        key = f"bulk_sel_{pid}"
                        if key not in st.session_state:
                            st.session_state[key] = False
                        bulk_marker = f"<div style='position:relative;top:-8px'>{'☑' if st.session_state.get(key) else '☐'}</div>"
                    else:
                        bulk_marker = ""
                    card_html = f"""
                        <div class="grid-card">
                            {bulk_marker}
                            {thumb_tag}
                            <div class="name">{name_html}<span class="small">#{number_html}</span></div>
                            <div class="meta">{role_html} • {sess_label}</div>
                        </div>
                    """
                    c.markdown(card_html, unsafe_allow_html=True)
                    acols = c.columns([1,1])
                    if acols[0].button("Edit", key=f"grid_edit_{pid}"):
                        st.session_state["editing_participant"] = pid
                        safe_rerun()
                    if acols[1].button("Delete", key=f"grid_del_{pid}"):
                        try:
                            with db_transaction() as conn:
                                if isinstance(p["photo_path"], str) and os.path.exists(p["photo_path"]):
                                    remove_media_file(p["photo_path"])
                                conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                                log_action(current_username, "delete_participant", p["name"] or "")
                                if st.session_state.get("editing_participant") == pid:
                                    st.session_state["editing_participant"] = None
                            try:
                                image_b64_for_path.clear()
                            except Exception:
                                pass
                            st.session_state["participants_offset"] = 0
                            st.warning("Participant deleted")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete participant: {e}")
                    if st.session_state.get("editing_participant") == pid:
                        with st.form(f"edit_participant_form_grid_{pid}"):
                            enumber = st.text_input("Number", value=safe_field(p, "number", ""), key=f"genumber_{pid}")
                            ename = st.text_input("Name", value=safe_field(p, "name", ""), key=f"gename_{pid}")
                            erole = st.text_input("Role", value=safe_field(p, "role", ""), key=f"gerole_{pid}")
                            eage = st.text_input("Age", value=safe_field(p, "age", ""), key=f"geage_{pid}")
                            eagency = st.text_input("Agency", value=safe_field(p, "agency", ""), key=f"geagency_{pid}")
                            eheight = st.text_input("Height", value=safe_field(p, "height", ""), key=f"geheight_{pid}")
                            ewaist = st.text_input("Waist", value=safe_field(p, "waist", ""), key=f"gewaist_{pid}")
                            edress = st.text_input("Dress/Suit", value=safe_field(p, "dress_suit", ""), key=f"gedress_{pid}")
                            eavail = st.text_input("Next Availability", value=safe_field(p, "availability", ""), key=f'geavail_{pid}')
                            ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"], key=f"gephoto_{pid}")
                            with db_connect() as conn:
                                sess_rows = list_sessions_for_project(conn, project_id)
                            assign_choices = [("Unassigned", None)] + [(s["name"], s["id"]) for s in sess_rows]
                            assign_labels = [c[0] for c in assign_choices]
                            default_idx = 0
                            if p["session_id"] is not None:
                                for i_c, copt in enumerate(assign_choices):
                                    if copt[1] == p["session_id"]:
                                        default_idx = i_c
                                        break
                            sel = st.selectbox("Assign session", assign_labels, index=default_idx, key=f"gassign_sel_{pid}")
                            save_edit = st.form_submit_button("Save Changes")
                            cancel_edit = st.form_submit_button("Cancel")
                            if save_edit:
                                try:
                                    with db_transaction() as conn:
                                        new_photo_path = p["photo_path"]
                                        if ephoto:
                                            new_photo_path = save_photo_file(ephoto, current_username, current)
                                            oldphoto = p["photo_path"]
                                            if isinstance(oldphoto, str) and os.path.exists(oldphoto):
                                                remove_media_file(oldphoto)
                                        target_sid = None
                                        if sel != "Unassigned":
                                            target_sid = next((c[1] for c in assign_choices if c[0] == sel), None)
                                        conn.execute("""
                                            UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?, session_id=?
                                            WHERE id=?
                                        """, (enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, target_sid, pid))
                                        log_action(current_username, "edit_participant", ename)
                                    try:
                                        image_b64_for_path.clear()
                                    except Exception:
                                        pass
                                    st.session_state["participants_offset"] = 0
                                    st.success("Participant updated!")
                                    st.session_state["editing_participant"] = None
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Unable to save participant edits: {e}")
                            if cancel_edit:
                                st.session_state["editing_participant"] = None
                                safe_rerun()

        # Load more / first page controls
        if offset + PAGE_SIZE < total:
            if st.button("Load more participants"):
                st.session_state["participants_offset"] = offset + PAGE_SIZE
                safe_rerun()
        if offset > 0:
            if st.button("Show first page"):
                st.session_state["participants_offset"] = 0
                safe_rerun()

        # Export to Word
        st.subheader("📄 Export Participants (Word)")
        if st.button("Download Word File of Current Project"):
            try:
                with db_connect() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                    parts = cur.fetchall()
                    if not parts:
                        st.info("No participants in this project yet.")
                    else:
                        sess_map = {s['id']: s['name'] for s in list_sessions_for_project(conn, project_id)}
                        doc = Document()
                        doc.add_heading(f"Participants - {active}", 0)
                        for p in parts:
                            table = doc.add_table(rows=1, cols=2)
                            table.autofit = False
                            table.columns[0].width = Inches(1.7)
                            table.columns[1].width = Inches(4.5)
                            row_cells = table.rows[0].cells

                            display_path = thumb_path_for(safe_field(p, "photo_path", ""))
                            bytes_data = None
                            if display_path and os.path.exists(display_path):
                                try:
                                    with open(display_path, "rb") as f:
                                        bytes_data = f.read()
                                except Exception:
                                    bytes_data = None
                            if bytes_data is None:
                                bytes_data = get_photo_bytes(safe_field(p, "photo_path", ""))

                            if bytes_data:
                                try:
                                    image_stream = io.BytesIO(bytes_data)
                                    image_stream.seek(0)
                                    paragraph = row_cells[0].paragraphs[0]
                                    run = paragraph.add_run()
                                    try:
                                        run.add_picture(image_stream, width=Inches(1.5))
                                    except Exception:
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

                            session_name = sess_map.get(p["session_id"], "Unassigned")
                            info_text = (
                                f"Number: {safe_field(p, 'number','')}\n"
                                f"Name: {safe_field(p, 'name','')}\n"
                                f"Session: {session_name}\n"
                                f"Role: {safe_field(p, 'role','')}\n"
                                f"Age: {safe_field(p, 'age','')}\n"
                                f"Agency: {safe_field(p, 'agency','')}\n"
                                f"Height: {safe_field(p, 'height','')}\n"
                                f"Waist: {safe_field(p, 'waist','')}\n"
                                f"Dress/Suit: {safe_field(p, 'dress_suit','')}\n"
                                f"Next Available: {safe_field(p, 'availability','')}"
                            )
                            row_cells[1].text = info_text
                            doc.add_paragraph("\n")

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

        # Admin dashboard (unchanged)
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
