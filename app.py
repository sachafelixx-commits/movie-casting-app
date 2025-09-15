# sachas_casting_manager_sqlite_sessions_full_backups_restore.py
"""
Sacha's Casting Manager
- SQLite backend with sessions support
- Letter-box participant cards
- Thumbnail generation & caching
- Admin tools: DB backup, combined DB+media zip, download backups, restore DB/media (safe)
- Robust schema ensure + migration from users.json
"""
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
import zipfile
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image, UnidentifiedImageError
import hashlib
from contextlib import contextmanager

# ========================
# Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager (SQLite + Sessions + Backup)", layout="wide")

DB_FILE = "data.db"
USERS_JSON = "users.json"   # used only for migration
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
BACKUPS_DIR = "backups"
DEFAULT_PROJECT_NAME = "Default Project"

# SQLite pragmas
PRAGMA_WAL = "WAL"
PRAGMA_SYNCHRONOUS = "NORMAL"

# ensure backups dir exists
os.makedirs(BACKUPS_DIR, exist_ok=True)

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
        # sqlite3.Row mapping-style access
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
# (creates a small thumbnail for display)
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
            base, _ = os.path.splitext(path)
            thumb = f"{base}_thumb.jpg"
            try:
                if os.path.exists(thumb):
                    os.remove(thumb)
            except Exception:
                pass
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
        c.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                session_date TEXT,
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
    Ensure sessions table exists and participants has session_id column.
    Also add missing columns to sessions if DB was created earlier with a smaller schema.
    """
    try:
        with db_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            if not cur.fetchone():
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        session_date TEXT,
                        description TEXT,
                        created_at TEXT,
                        FOREIGN KEY (project_id) REFERENCES projects(id)
                    );
                """)
            cur.execute("PRAGMA table_info(participants)")
            cols = [r[1] for r in cur.fetchall()]
            if "session_id" not in cols:
                try:
                    cur.execute("ALTER TABLE participants ADD COLUMN session_id INTEGER;")
                except Exception:
                    pass
            cur.execute("PRAGMA table_info(sessions)")
            scols = [r[1] for r in cur.fetchall()]
            desired = {
                "session_date": "TEXT",
                "description": "TEXT",
                "created_at": "TEXT"
            }
            for col, ctype in desired.items():
                if col not in scols:
                    try:
                        cur.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ctype};")
                    except Exception:
                        pass
            conn.commit()
    except Exception:
        pass

# ------------------------
# log_action - needed early
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
# Initialize DB + migrate once + ensure schema
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

# Session helpers
def list_sessions_for_project(conn, project_id):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY created_at", (project_id,))
    return c.fetchall()

def create_session(conn, project_id, name, session_date=None, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute("INSERT INTO sessions (project_id, name, session_date, description, created_at) VALUES (?, ?, ?, ?, ?)",
                  (project_id, name, session_date, description, now))
    except sqlite3.OperationalError:
        try:
            c.execute("INSERT INTO sessions (project_id, name, created_at) VALUES (?, ?, ?)", (project_id, name, now))
        except Exception:
            raise
    return c.lastrowid

def get_session_by_id(conn, session_id):
    if not session_id:
        return None
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    return c.fetchone()

def update_session(conn, session_id, name, session_date, description):
    c = conn.cursor()
    try:
        c.execute("UPDATE sessions SET name=?, session_date=?, description=? WHERE id=?", (name, session_date, description, session_id))
    except sqlite3.OperationalError:
        try:
            c.execute("UPDATE sessions SET name=?, session_date=? WHERE id=?", (name, session_date, session_id))
        except Exception:
            c.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))

def delete_session(conn, session_id):
    c = conn.cursor()
    c.execute("UPDATE participants SET session_id=NULL WHERE session_id=?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

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
# Backup & Restore helpers
# ========================
def make_db_backup():
    """Copy current data.db to backups/ with timestamp and return path (or None)."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUPS_DIR, f"data.db.backup.{ts}.sqlite")
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, backup_path)
            return backup_path
        return None
    except Exception:
        return None

def make_media_backup():
    """Create a zip archive of media/ into backups/, return path or None."""
    try:
        if not os.path.exists(MEDIA_DIR):
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_base = os.path.join(BACKUPS_DIR, f"media_backup_{ts}")
        # make_archive will append .zip
        shutil.make_archive(archive_base, 'zip', MEDIA_DIR)
        return archive_base + ".zip"
    except Exception:
        return None

def make_combined_backup():
    """Create a single zip containing data.db and media/ for easy download/restore."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmpdir = tempfile.mkdtemp(prefix="backup_tmp_")
        try:
            # copy DB
            if os.path.exists(DB_FILE):
                shutil.copy2(DB_FILE, os.path.join(tmpdir, "data.db"))
            # copy media if exists
            if os.path.exists(MEDIA_DIR):
                shutil.copytree(MEDIA_DIR, os.path.join(tmpdir, "media"))
            archive_name = os.path.join(BACKUPS_DIR, f"full_backup_{ts}.zip")
            shutil.make_archive(os.path.splitext(archive_name)[0], 'zip', tmpdir)
            return archive_name
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass
    except Exception:
        return None

def list_backups():
    """Return list of backup file names sorted by mtime desc."""
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        files = [f for f in os.listdir(BACKUPS_DIR) if os.path.isfile(os.path.join(BACKUPS_DIR, f))]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(BACKUPS_DIR, x)), reverse=True)
        return files
    except Exception:
        return []

def download_file_bytes(path):
    """Return bytes for a given path or None."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None

def integrity_check_db_file(path):
    """Run PRAGMA integrity_check on a DB file at path. Returns (ok_bool, message)."""
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()
        conn.close()
        if res and isinstance(res[0], str) and res[0].lower().strip() == "ok":
            return True, "ok"
        else:
            return False, res[0] if res else "integrity_check failed"
    except Exception as e:
        return False, f"error: {e}"

def restore_db_from_uploaded(uploaded_file, create_backup=True):
    """
    uploaded_file is a Streamlit UploadedFile.
    This will:
      - optionally backup current DB
      - write uploaded_file to a temporary file
      - integrity check it
      - if ok, overwrite DB_FILE
    Returns (success_bool, message)
    """
    if not uploaded_file:
        return False, "No DB file provided."
    try:
        # close the active connection before overwriting the file
        get_db_conn().close()
        
        if create_backup:
            make_db_backup()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        data = uploaded_file.read()
        with open(tmp.name, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        ok, msg = integrity_check_db_file(tmp.name)
        if not ok:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            return False, f"DB integrity check failed: {msg}"
        # everything ok - move into place
        try:
            shutil.copy2(tmp.name, DB_FILE)
            st.cache_resource.clear()
        except Exception as e:
            try:
                # try replace via writing bytes
                with open(DB_FILE, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e2:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
                return False, f"Unable to write DB file: {e} / {e2}"
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        safe_rerun()
        return True, "DB restored successfully."
    except Exception as e:
        return False, f"Restore failed: {e}"

def restore_media_from_uploaded(uploaded_zip, create_backup=True):
    """
    uploaded_zip is a Streamlit UploadedFile (zip).
    Behavior:
    - optionally backup current media/ to backups/
    - extract uploaded zip to a temp dir and then atomically replace MEDIA_DIR
    Returns (success_bool, message)
    """
    if not uploaded_zip:
        return False, "No media zip provided."
    try:
        if create_backup:
            make_media_backup()
        tmpdir = tempfile.mkdtemp(prefix="media_restore_tmp_")
        tmpzip = os.path.join(tmpdir, "upload.zip")
        try:
            uploaded_zip.seek(0)
        except Exception:
            pass
        with open(tmpzip, "wb") as f:
            f.write(uploaded_zip.read())
        try:
            with zipfile.ZipFile(tmpzip, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
            # Find the media folder inside the extracted content
            extracted_media_path = None
            for root, dirs, files in os.walk(tmpdir):
                for d in dirs:
                    if d == "media":
                        extracted_media_path = os.path.join(root, d)
                        break
                if extracted_media_path:
                    break
            if not extracted_media_path:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return False, "Could not find 'media' folder in uploaded zip."
            # Atomically replace
            if os.path.exists(MEDIA_DIR):
                shutil.rmtree(MEDIA_DIR) # Add this to make sure the old media directory is removed
            shutil.move(extracted_media_path, MEDIA_DIR)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        safe_rerun()
        return True, "Media restored successfully."
    except Exception as e:
        return False, f"Restore failed: {e}"

def restore_combined_from_uploaded(uploaded_zip, create_backup=True):
    """
    Restores both DB and media from a single zip.
    """
    if not uploaded_zip:
        return False, "No zip provided."
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmpdir = tempfile.mkdtemp(prefix="full_restore_tmp_")
        try:
            uploaded_zip.seek(0)
            tmp_zip_path = os.path.join(tmpdir, "uploaded.zip")
            with open(tmp_zip_path, "wb") as f:
                f.write(uploaded_zip.read())
            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
            extracted_db_path = os.path.join(tmpdir, "data.db")
            if not os.path.exists(extracted_db_path):
                shutil.rmtree(tmpdir, ignore_errors=True)
                return False, "Could not find 'data.db' in the zip file."
            
            # Restore DB
            get_db_conn().close()
            if create_backup:
                make_db_backup()
            shutil.copy2(extracted_db_path, DB_FILE)
            st.cache_resource.clear()

            # Restore media
            extracted_media_path = os.path.join(tmpdir, "media")
            if os.path.exists(extracted_media_path):
                if create_backup:
                    make_media_backup()
                if os.path.exists(MEDIA_DIR):
                    shutil.rmtree(MEDIA_DIR)
                shutil.move(extracted_media_path, MEDIA_DIR)
            
            shutil.rmtree(tmpdir, ignore_errors=True)
            safe_rerun()
            return True, "Full backup restored successfully."
        except Exception as e:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False, f"Full restore failed: {e}"
    except Exception as e:
        return False, f"Restore failed: {e}"


# ========================
# UI: Auth + main app
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
if "editing_participant" not in st.session_state:
    st.session_state["editing_participant"] = None
if "filter_name" not in st.session_state:
    st.session_state["filter_name"] = ""
if "filter_role" not in st.session_state:
    st.session_state["filter_role"] = ""
if "editing_session" not in st.session_state:
    st.session_state["editing_session"] = None
if "_needs_refresh" not in st.session_state:
    st.session_state["_needs_refresh"] = False

if st.session_state.get("_needs_refresh"):
    safe_rerun()

conn = get_db_conn()

# ========================
# UI
# ========================

def login_form():
    st.header("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        user = get_user_by_username(conn, username)
        if user and hash_password(password) == user["password"]:
            st.session_state["logged_in"] = True
            st.session_state["current_user"] = dict(user)
            log_action(username, "login")
            update_user_last_login(conn, user["id"])
            st.success(f"Welcome, {username}!")
            safe_rerun()
        else:
            st.error("Invalid username or password.")
    if st.button("Create Admin User"):
        try:
            if not get_user_by_username(conn, "admin"):
                create_user(conn, "admin", hash_password("supersecret"), "Admin")
                conn.commit()
                st.success("Default 'admin' user with password 'supersecret' created. Please log in.")
            else:
                st.warning("Admin user already exists.")
        except Exception as e:
            st.error(f"Error creating admin user: {e}")

if not st.session_state["logged_in"]:
    login_form()
else:
    current_user_id = st.session_state["current_user"]["id"]
    current_username = st.session_state["current_user"]["username"]
    role = st.session_state["current_user"]["role"]

    st.sidebar.header(f"Welcome, {current_username}")
    st.sidebar.markdown(f"**Role**: {role}")

    # ========================
    # UI: Project & User Management
    # ========================
    with st.sidebar.expander("💼 My Projects", expanded=True):
        projects = list_projects_with_counts(conn, current_user_id)
        current = st.session_state["current_project_name"]

        if not projects and st.session_state.get("current_project_name"):
             st.session_state["current_project_name"] = None
             safe_rerun()
        
        project_names = [p["name"] for p in projects]
        project_names.sort(key=lambda x: x.lower())
        
        if not current and project_names:
            st.session_state["current_project_name"] = project_names[0]
            current = project_names[0]

        selected_project_name = st.radio(
            f"Select a project:",
            project_names,
            index=project_names.index(current) if current in project_names else (0 if project_names else None),
            key="project_selector",
            label_visibility="collapsed"
        )
        if selected_project_name and selected_project_name != current:
            st.session_state["current_project_name"] = selected_project_name
            st.session_state["participant_mode"] = False
            safe_rerun()
        if not selected_project_name:
            st.write("You don't have any projects yet.")

    with st.sidebar.form("new_project_form"):
        st.subheader("Create New Project")
        new_project_name = st.text_input("Project Name (2-50 chars)", max_chars=50)
        new_project_description = st.text_area("Description (optional)", max_chars=500)
        if st.form_submit_button("Create Project"):
            if not new_project_name or len(new_project_name.strip()) < 2:
                st.error("Project name must be at least 2 characters.")
            else:
                try:
                    create_project(conn, current_user_id, new_project_name.strip(), new_project_description.strip())
                    conn.commit()
                    log_action(current_username, "create_project", f"name={new_project_name}")
                    st.success(f"Project '{new_project_name}' created!")
                    st.session_state["current_project_name"] = new_project_name.strip()
                    safe_rerun()
                except sqlite3.IntegrityError:
                    st.error(f"A project with the name '{new_project_name}' already exists.")
                except Exception as e:
                    st.error(f"Error creating project: {e}")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["current_project_name"] = None
        log_action(current_username, "logout")
        st.success("Logged out successfully.")
        safe_rerun()
    
    # ========================
    # UI: Main App
    # ========================

    if st.session_state["current_project_name"]:
        current_project = get_project_by_name(conn, current_user_id, st.session_state["current_project_name"])
        if not current_project:
            st.session_state["current_project_name"] = None
            safe_rerun()
        
        current_project_id = current_project["id"]

        st.header(f"💼 Project: {st.session_state['current_project_name']}")
        st.write(f"_{current_project['description'] or 'No description'}_")

        # ------------------------
        # Edit/Delete Project
        # ------------------------
        with st.expander("🛠️ Project Settings", expanded=False):
            col1, col2 = st.columns(2)
            with col1.form("edit_project_form"):
                st.subheader("Edit Project Details")
                new_name = st.text_input("New Project Name", value=current_project["name"])
                new_desc = st.text_area("New Description", value=current_project["description"] or "")
                if st.form_submit_button("Update Project"):
                    if not new_name or len(new_name.strip()) < 2:
                        st.error("Project name must be at least 2 characters.")
                    else:
                        try:
                            c = conn.cursor()
                            c.execute("UPDATE projects SET name=?, description=? WHERE id=?", (new_name, new_desc, current_project_id))
                            conn.commit()
                            log_action(current_username, "edit_project", f"project={current_project['name']} -> {new_name}")
                            
                            # also rename the media folder
                            if new_name != current_project["name"]:
                                rename_project_move_media(current_project["name"], new_name, current_username)
                                
                            st.success("Project updated!")
                            st.session_state["current_project_name"] = new_name
                            safe_rerun()
                        except sqlite3.IntegrityError:
                            st.error(f"A project with the name '{new_name}' already exists.")
                        except Exception as e:
                            st.error(f"Error updating project: {e}")

            with col2.form("delete_project_form"):
                st.subheader("Delete Project")
                st.markdown(f"**Warning**: This action cannot be undone. All participants and data associated with the project '{current_project['name']}' will be permanently deleted. This will not affect other projects.")
                if st.form_submit_button("Permanently Delete This Project"):
                    try:
                        c = conn.cursor()
                        c.execute("DELETE FROM sessions WHERE project_id=?", (current_project_id,))
                        c.execute("DELETE FROM participants WHERE project_id=?", (current_project_id,))
                        c.execute("DELETE FROM projects WHERE id=?", (current_project_id,))
                        conn.commit()
                        log_action(current_username, "delete_project", f"name={current_project['name']}")
                        delete_project_media(current_username, current_project["name"])
                        st.success(f"Project '{current_project['name']}' and all its data have been deleted.")
                        st.session_state["current_project_name"] = None
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Error deleting project: {e}")

        st.markdown("---")

        # ------------------------
        # Sessions
        # ------------------------
        st.subheader("🎬 Sessions")
        sessions = list_sessions_for_project(conn, current_project_id)
        if not sessions:
            st.write("No sessions created yet.")

        with st.form("new_session_form"):
            st.write("Create a New Session")
            new_session_name = st.text_input("Session Name (e.g., '10am group', 'Wednesday callbacks')", max_chars=100)
            new_session_date = st.date_input("Session Date (optional)", value=None)
            new_session_description = st.text_area("Session Description (optional)", max_chars=500)
            if st.form_submit_button("Create Session"):
                if new_session_name.strip():
                    create_session(conn, current_project_id, new_session_name.strip(), new_session_date, new_session_description)
                    conn.commit()
                    log_action(current_username, "create_session", f"project={current_project['name']}, session={new_session_name}")
                    st.success(f"Session '{new_session_name}' created.")
                    safe_rerun()
                else:
                    st.error("Session name cannot be empty.")

        if sessions:
            st.markdown("---")
            for sesh in sessions:
                with st.container():
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{sesh['name']}**")
                        if sesh['session_date']:
                             st.write(f"Date: {sesh['session_date']}")
                        if sesh['description']:
                             st.write(f"_{sesh['description']}_")
                    with col2:
                        edit_btn = st.button("Edit", key=f"edit_session_{sesh['id']}")
                        delete_btn = st.button("Delete", key=f"delete_session_{sesh['id']}")
                    if edit_btn:
                        st.session_state["editing_session"] = dict(sesh)
                        safe_rerun()
                    if delete_btn:
                        if st.session_state.get("editing_session", {}).get("id") == sesh["id"]:
                             st.session_state["editing_session"] = None
                        delete_session(conn, sesh["id"])
                        conn.commit()
                        log_action(current_username, "delete_session", f"project={current_project['name']}, session={sesh['name']}")
                        st.warning(f"Session '{sesh['name']}' deleted.")
                        safe_rerun()
            
            if st.session_state.get("editing_session"):
                sesh_edit = st.session_state["editing_session"]
                with st.expander(f"Editing Session: {sesh_edit['name']}", expanded=True):
                    with st.form(key=f"edit_sesh_form_{sesh_edit['id']}"):
                        new_sesh_name = st.text_input("New Name", value=sesh_edit['name'])
                        new_sesh_date = st.date_input("New Date", value=datetime.strptime(sesh_edit['session_date'], "%Y-%m-%d").date() if sesh_edit['session_date'] else None)
                        new_sesh_desc = st.text_area("New Description", value=sesh_edit['description'] or "")
                        col_e1, col_e2 = st.columns([1,1])
                        if col_e1.form_submit_button("Update Session"):
                            update_session(conn, sesh_edit['id'], new_sesh_name, new_sesh_date, new_sesh_desc)
                            conn.commit()
                            log_action(current_username, "update_session", f"project={current_project['name']}, session={sesh_edit['name']}")
                            st.success("Session updated.")
                            st.session_state["editing_session"] = None
                            safe_rerun()
                        if col_e2.form_submit_button("Cancel"):
                            st.session_state["editing_session"] = None
                            safe_rerun()
        
        st.markdown("---")

        # ------------------------
        # Participant Management
        # ------------------------
        st.subheader("👥 Participants")
        current_session_id = None
        current_session_name = "Unassigned"
        selected_session = st.selectbox("Filter by Session:", ["All", "Unassigned"] + [s['name'] for s in sessions])
        if selected_session == "All":
            participants = conn.execute("SELECT * FROM participants WHERE project_id=? ORDER BY name COLLATE NOCASE", (current_project_id,)).fetchall()
        elif selected_session == "Unassigned":
            participants = conn.execute("SELECT * FROM participants WHERE project_id=? AND session_id IS NULL ORDER BY name COLLATE NOCASE", (current_project_id,)).fetchall()
        else:
            session_info = next((s for s in sessions if s['name'] == selected_session), None)
            if session_info:
                current_session_id = session_info["id"]
                current_session_name = session_info["name"]
                participants = conn.execute("SELECT * FROM participants WHERE project_id=? AND session_id=? ORDER BY name COLLATE NOCASE", (current_project_id, current_session_id)).fetchall()

        # Filtering UI
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.session_state["filter_name"] = st.text_input("Filter by Name:", st.session_state["filter_name"])
        with col_f2:
            st.session_state["filter_role"] = st.text_input("Filter by Role:", st.session_state["filter_role"])

        filtered_participants = [p for p in participants if st.session_state["filter_name"].lower() in safe_field(p, 'name').lower() and st.session_state["filter_role"].lower() in safe_field(p, 'role').lower()]

        st.markdown("---")

        if st.button("➕ Add New Participant"):
            st.session_state["editing_participant"] = "new"
            st.session_state["participant_mode"] = True
            safe_rerun()

        if st.session_state["participant_mode"] or st.session_state["editing_participant"]:
            # ------------------------
            # Add/Edit Participant
            # ------------------------
            is_new = st.session_state["editing_participant"] == "new"
            part = {}
            if not is_new:
                part_id = st.session_state["editing_participant"]
                part = conn.execute("SELECT * FROM participants WHERE id=?", (part_id,)).fetchone()
            
            st.subheader(f"{'Add New' if is_new else 'Edit'} Participant")
            with st.form("participant_form"):
                col_n1, col_n2 = st.columns(2)
                with col_n1:
                    name = st.text_input("Name", value=safe_field(part, 'name'), max_chars=100)
                    role_p = st.text_input("Role", value=safe_field(part, 'role'), max_chars=100)
                    age = st.text_input("Age", value=safe_field(part, 'age'), max_chars=100)
                    agency = st.text_input("Agency", value=safe_field(part, 'agency'), max_chars=100)
                    number = st.text_input("Number", value=safe_field(part, 'number'), max_chars=100)
                with col_n2:
                    height = st.text_input("Height", value=safe_field(part, 'height'), max_chars=100)
                    waist = st.text_input("Waist", value=safe_field(part, 'waist'), max_chars=100)
                    dress_suit = st.text_input("Dress/Suit Size", value=safe_field(part, 'dress_suit'), max_chars=100)
                    availability = st.text_area("Availability", value=safe_field(part, 'availability'), max_chars=500)
                    photo_file = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png", "gif", "webp"])
                
                session_options = ["(None)"] + [s['name'] for s in sessions]
                current_session_idx = 0
                if safe_field(part, "session_id"):
                    sesh = get_session_by_id(conn, safe_field(part, "session_id"))
                    if sesh:
                        current_session_idx = session_options.index(sesh["name"])
                selected_session_for_part = st.selectbox("Assign to Session:", session_options, index=current_session_idx)
                selected_session_id_for_part = next((s['id'] for s in sessions if s['name'] == selected_session_for_part), None)
                
                col_s1, col_s2 = st.columns([1,1])
                with col_s1:
                    if st.form_submit_button("Save"):
                        if not name.strip():
                            st.error("Name cannot be empty.")
                        else:
                            photo_path = safe_field(part, "photo_path")
                            if photo_file:
                                if not is_new and photo_path:
                                    remove_media_file(photo_path)
                                photo_path = save_photo_file(photo_file, current_username, current_project["name"])

                            if is_new:
                                try:
                                    conn.execute("""
                                        INSERT INTO participants 
                                        (project_id, session_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """, (current_project_id, selected_session_id_for_part, number.strip(), name.strip(), role_p.strip(), age.strip(), agency.strip(), height.strip(), waist.strip(), dress_suit.strip(), availability.strip(), photo_path))
                                    conn.commit()
                                    log_action(current_username, "add_participant", f"project={current_project['name']}, name={name}")
                                    st.success(f"Participant '{name}' added.")
                                    st.session_state["editing_participant"] = None
                                    st.session_state["participant_mode"] = False
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Error adding participant: {e}")
                            else:
                                try:
                                    conn.execute("""
                                        UPDATE participants
                                        SET session_id=?, number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?
                                        WHERE id=?
                                    """, (selected_session_id_for_part, number.strip(), name.strip(), role_p.strip(), age.strip(), agency.strip(), height.strip(), waist.strip(), dress_suit.strip(), availability.strip(), photo_path, part['id']))
                                    conn.commit()
                                    log_action(current_username, "edit_participant", f"project={current_project['name']}, name={name}")
                                    st.success(f"Participant '{name}' updated.")
                                    st.session_state["editing_participant"] = None
                                    st.session_state["participant_mode"] = False
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Error updating participant: {e}")
                
                with col_s2:
                    if st.form_submit_button("Cancel"):
                        st.session_state["editing_participant"] = None
                        st.session_state["participant_mode"] = False
                        safe_rerun()
                
            st.markdown("---")

        # ------------------------
        # Display Participants
        # ------------------------
        st.subheader(f"Total Participants: {len(filtered_participants)}")

        if not filtered_participants:
            st.write("No participants match your filter criteria.")
        else:
            for p in filtered_participants:
                st.markdown(f'<div class="part-row">', unsafe_allow_html=True)
                with st.columns([1, 4])[1]:
                    st.markdown(f'<div class="participant-letterbox">', unsafe_allow_html=True)
                    photo_path = p["photo_path"]
                    thumb_p = thumb_path_for(photo_path)
                    
                    if thumb_p and os.path.exists(thumb_p):
                        try:
                            thumb_b64 = image_b64_for_path(thumb_p)
                            st.markdown(f"""<img class="photo" src="{thumb_b64}">""", unsafe_allow_html=True)
                        except Exception:
                            st.image("https://via.placeholder.com/400x400.png?text=No+Photo", use_column_width=True)
                    else:
                        st.image("https://via.placeholder.com/400x400.png?text=No+Photo", use_column_width=True)

                    st.markdown(f'<p class="name">{safe_field(p, "name")}</p>', unsafe_allow_html=True)
                    if p["role"]:
                        st.markdown(f'<p class="meta">**Role**: {safe_field(p, "role")}</p>', unsafe_allow_html=True)
                    
                    sesh = get_session_by_id(conn, safe_field(p, "session_id"))
                    sesh_name = safe_field(sesh, "name") if sesh else "Unassigned"
                    st.markdown(f'<p class="small">Session: {sesh_name}</p>', unsafe_allow_html=True)
                    
                    st.markdown(f'<p class="small">**Age**: {safe_field(p, "age")}</p>', unsafe_allow_html=True)
                    st.markdown(f'<p class="small">**Agency**: {safe_field(p, "agency")}</p>', unsafe_allow_html=True)
                    
                    if p["height"] or p["waist"] or p["dress_suit"]:
                        st.markdown(f'<p class="meta">**Sizes**: {safe_field(p, "height")} / {safe_field(p, "waist")} / {safe_field(p, "dress_suit")}</p>', unsafe_allow_html=True)
                    if p["availability"]:
                        st.markdown(f'<p class="meta">**Availability**: {safe_field(p, "availability")}</p>', unsafe_allow_html=True)
                    
                    col_p1, col_p2 = st.columns(2)
                    if col_p1.button("✏️ Edit", key=f"edit_part_{p['id']}"):
                        st.session_state["editing_participant"] = p["id"]
                        st.session_state["participant_mode"] = True
                        safe_rerun()
                    if col_p2.button("🗑️ Delete", key=f"delete_part_{p['id']}"):
                        try:
                            remove_media_file(p["photo_path"])
                            conn.execute("DELETE FROM participants WHERE id=?", (p["id"],))
                            conn.commit()
                            log_action(current_username, "delete_participant", f"project={current_project['name']}, name={p['name']}")
                            st.warning(f"Participant '{p['name']}' deleted.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete participant: {e}")
                    
                    st.markdown(f'</div>', unsafe_allow_html=True)
                st.markdown(f'</div>', unsafe_allow_html=True)
        
        # ------------------------
        # Word Export
        # ------------------------
        st.markdown("---")
        st.subheader("📄 Export Participants to Word")
        try:
            doc = Document()
            doc.add_heading(f"Participants for Project: {st.session_state['current_project_name']}", level=1)
            doc.add_paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            for p in filtered_participants:
                doc.add_heading(p["name"], level=2)
                
                # Check for photo and add it
                photo_path = p["photo_path"]
                if photo_path and os.path.exists(photo_path):
                    doc.add_picture(photo_path, width=Inches(3))
                
                doc.add_paragraph(f"Role: {safe_field(p, 'role')}")
                doc.add_paragraph(f"Age: {safe_field(p, 'age')}")
                doc.add_paragraph(f"Agency: {safe_field(p, 'agency')}")
                doc.add_paragraph(f"Sizes: {safe_field(p, 'height')} / {safe_field(p, 'waist')} / {safe_field(p, 'dress_suit')}")
                doc.add_paragraph(f"Availability: {safe_field(p, 'availability')}")
                doc.add_paragraph("-" * 20)
            
            word_stream = io.BytesIO()
            doc.save(word_stream)
            word_stream.seek(0)
            st.download_button(
                label="Click to download Word file",
                data=word_stream,
                file_name=f"{current_project['name']}_participants.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        except Exception as e:
            st.error(f"Unable to generate Word file: {e}")

# ------------------------
# Admin dashboard + backup & restore UI
# ------------------------
if role == "Admin":
    st.header("👑 Admin Dashboard")
    if st.button("🔄 Refresh Users"):
        safe_rerun()
    
    # Backup tools
    with st.expander("🗄️ Backups & Restore (Admin)", expanded=False):
        st.write("Create backups, download existing backups, or restore DB/media from uploads. **Always create a backup first.**")
        
        col1, col2, col3 = st.columns([2,2,3])
        if col1.button("Create DB Backup"):
            bp = make_db_backup()
            if bp:
                st.success(f"DB backup created: {bp}")
            else:
                st.error("Failed to create DB backup.")
        if col2.button("Create Media Backup"):
            bp = make_media_backup()
            if bp:
                st.success(f"Media backup created: {bp}")
            else:
                st.error("Failed to create media backup.")
        if col3.button("Create Combined Backup"):
            bp = make_combined_backup()
            if bp:
                st.success(f"Combined backup created: {bp}")
            else:
                st.error("Failed to create combined backup.")
        
        st.markdown("---")
        st.subheader("Download existing backups")
        backups = list_backups()
        if backups:
            for f in backups:
                col_d1, col_d2 = st.columns([4, 1])
                with col_d1:
                    st.write(f)
                with col_d2:
                    st.download_button(
                        label="Download",
                        data=download_file_bytes(os.path.join(BACKUPS_DIR, f)),
                        file_name=f,
                        key=f"dl_{f}"
                    )
        else:
            st.write("No backups found.")

        st.markdown("---")
        st.subheader("Restore from Uploaded Backup")
        uploaded_db_file = st.file_uploader("Upload DB backup (.sqlite)", type=["sqlite"])
        if uploaded_db_file:
            if st.button("Restore DB from uploaded file"):
                with st.spinner("Restoring DB..."):
                    ok, msg = restore_db_from_uploaded(uploaded_db_file)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
        
        uploaded_media_zip = st.file_uploader("Upload Media backup (.zip)", type=["zip"])
        if uploaded_media_zip:
            if st.button("Restore Media from uploaded zip"):
                with st.spinner("Restoring media..."):
                    ok, msg = restore_media_from_uploaded(uploaded_media_zip)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
        
        uploaded_full_zip = st.file_uploader("Upload Combined backup (.zip)", type=["zip"])
        if uploaded_full_zip:
            if st.button("Restore Both DB and Media from zip"):
                with st.spinner("Performing full restore..."):
                    ok, msg = restore_combined_from_uploaded(uploaded_full_zip)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
    
    # ------------------------
    # User Management
    # ------------------------
    with st.expander("👤 User Management", expanded=False):
        st.subheader("Manage Users")
        users = conn.execute("SELECT * FROM users").fetchall()
        for user in users:
            if user["username"] == current_username and user["role"] == "Admin" and len(users) == 1:
                st.info("You are the only user on this system. Cannot delete yourself.")
                continue
            col_u1, col_u2, col_u3 = st.columns([3, 2, 2])
            with col_u1:
                st.markdown(f"**{user['username']}** ({user['role']})")
                st.markdown(f"_{safe_field(user, 'last_login')}_")
            with col_u2:
                if user['username'] != current_username:
                    if st.button("Set to Admin", key=f"admin_btn_{user['id']}"):
                        conn.execute("UPDATE users SET role='Admin' WHERE id=?", (user['id'],))
                        conn.commit()
                        st.success(f"{user['username']} is now an Admin.")
                        safe_rerun()
            with col_u3:
                if user['username'] != current_username:
                    if st.button("Delete User", key=f"del_btn_{user['id']}"):
                        try:
                            # find all participants and delete their photos
                            cur = conn.cursor()
                            cur.execute("SELECT id FROM users WHERE username=?", (user['username'],))
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
                                conn.commit()
                                log_action(current_username, "delete_user", user['username'])
                            st.warning(f"User {user['username']} deleted.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete user: {e}")
