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
        st.rerun()
        return
    except Exception:
        pass
    # Fallback for older Streamlit versions
    try:
        st.experimental_rerun()
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
            ext = ".jpg"  # Default fallback
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
    """
    Safely removes a file and its corresponding thumbnail,
    and then recursively removes any empty parent directories.
    """
    if not path or not isinstance(path, str):
        return

    # Ensure path is within the designated media directory
    safe_path = os.path.abspath(path)
    media_path = os.path.abspath(MEDIA_DIR)
    if not safe_path.startswith(media_path):
        return

    try:
        # Remove the main photo file
        if os.path.exists(safe_path):
            os.remove(safe_path)
            
        # Remove the thumbnail
        base, _ = os.path.splitext(safe_path)
        thumb = f"{base}_thumb.jpg"
        if os.path.exists(thumb):
            os.remove(thumb)

        # Recursively remove empty parent directories
        parent = os.path.dirname(safe_path)
        while parent and os.path.abspath(parent) != media_path:
            try:
                if not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    # Directory is not empty, stop
                    break
            except OSError:
                # Directory is in use or we don't have permissions, stop
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
    if isinstance(photo_field, str):
        if os.path.exists(photo_field):
            try:
                with open(photo_field, "rb") as f:
                    return f.read()
            except Exception:
                return None
        else:
            try:
                return base64.b64decode(photo_field)
            except Exception:
                return None
    return None

# ========================
# SQLite helpers
# ========================
def db_connect():
    return get_db_conn()

@contextmanager
def db_transaction():
    conn = get_db_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

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
        with open(MIGRATION_MARKer, "w", encoding="utf-8") as f:
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

def get_project_by_id(conn, project_id):
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE id=?", (project_id,))
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
            if not os.path.exists(new_dir):
                shutil.move(old_dir, new_dir)
            else:
                # Merge contents if new directory exists
                for item in os.listdir(old_dir):
                    s = os.path.join(old_dir, item)
                    d = os.path.join(new_dir, item)
                    if os.path.isdir(s):
                        shutil.move(s, d)
                    else:
                        shutil.move(s, d)
                os.rmdir(old_dir)
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
        archive_name_base = f"full_backup_{ts}"
        archive_name = os.path.join(BACKUPS_DIR, archive_name_base)
        tmpdir = tempfile.mkdtemp(prefix="backup_tmp_")
        try:
            # Copy DB to temp dir
            if os.path.exists(DB_FILE):
                shutil.copy2(DB_FILE, os.path.join(tmpdir, "data.db"))
            # Copy media if exists
            if os.path.exists(MEDIA_DIR):
                shutil.copytree(MEDIA_DIR, os.path.join(tmpdir, "media"))
            
            shutil.make_archive(archive_name, 'zip', root_dir=tmpdir)
            return archive_name + ".zip"
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
    """Run PRAGMA integrity_check on a DB file at path.
    Returns (ok_bool, message)."""
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
    Safely restores a DB from an uploaded file.
    """
    if not uploaded_file:
        return False, "No DB file provided."
    if create_backup:
        make_db_backup()
    
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as tmp:
            tmp_path = tmp.name
            data = uploaded_file.read()
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())

        ok, msg = integrity_check_db_file(tmp_path)
        if not ok:
            return False, f"DB integrity check failed: {msg}"
        
        # Everything OK - replace the main database file
        shutil.copy2(tmp_path, DB_FILE)
        return True, "Database restored successfully."
    except Exception as e:
        return False, f"An error occurred during restore: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

def restore_media_from_uploaded(uploaded_file, create_backup=True):
    """
    Restores media from an uploaded zip file.
    """
    if not uploaded_file:
        return False, "No media file provided."
    if create_backup:
        make_media_backup()

    try:
        with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
            # Ensure the zip file contains a directory named "media"
            root_dir_name = os.path.commonpath(zip_ref.namelist())
            if not root_dir_name.startswith("media"):
                return False, "Invalid zip file. It should contain a 'media' directory."

            zip_ref.extractall(path=os.getcwd())
        return True, "Media restored successfully."
    except Exception as e:
        return False, f"An error occurred during media restore: {e}"

def restore_combined_from_uploaded(uploaded_file, create_backup=True):
    """
    Restores both DB and media from a combined zip file.
    """
    if not uploaded_file:
        return False, "No combined backup file provided."
    if create_backup:
        make_combined_backup()
        
    tmpdir = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
            
            # Restore DB
            db_path = os.path.join(tmpdir, "data.db")
            if os.path.exists(db_path):
                ok, msg = integrity_check_db_file(db_path)
                if not ok:
                    return False, f"Combined backup failed: DB integrity check failed: {msg}"
                shutil.copy2(db_path, DB_FILE)
            else:
                return False, "Combined backup failed: 'data.db' not found in zip file."
            
            # Restore Media
            media_path = os.path.join(tmpdir, "media")
            if os.path.exists(media_path):
                if os.path.exists(MEDIA_DIR):
                    shutil.rmtree(MEDIA_DIR)
                shutil.move(media_path, MEDIA_DIR)
            
            return True, "Combined backup restored successfully."
    except Exception as e:
        return False, f"An error occurred during combined restore: {e}"

# ========================
# Authentication & Sessions
# ========================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "role" not in st.session_state:
    st.session_state.role = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "current_project_id" not in st.session_state:
    st.session_state.current_project_id = None

if st.session_state.logged_in:
    current_username = st.session_state.username
    role = st.session_state.role
    user_id = st.session_state.user_id
    
    st.sidebar.markdown(f"**Logged in as:** {current_username} ({role})")
    
    with db_transaction() as conn:
        projects = list_projects_for_user(conn, user_id)
        
    if not projects:
        st.warning("You have no projects. Please create one.")
        with st.form("new_project_form_no_projects"):
            new_project_name = st.text_input("New Project Name", placeholder="e.g., Spring Campaign 2025")
            new_project_description = st.text_area("Description (optional)")
            if st.form_submit_button("Create Project"):
                if new_project_name:
                    with db_transaction() as conn:
                        try:
                            pid = create_project(conn, user_id, new_project_name, new_project_description)
                            st.session_state.current_project_id = pid
                            log_action(current_username, "create_project", new_project_name)
                            st.success(f"Project '{new_project_name}' created.")
                            safe_rerun()
                        except sqlite3.IntegrityError:
                            st.error("A project with this name already exists.")
                        except Exception as e:
                            st.error(f"Unable to create project: {e}")
                else:
                    st.error("Project name cannot be empty.")
    else:
        project_names = [p["name"] for p in projects]
        project_map = {p["name"]: p for p in projects}
        
        # Determine current project
        current_project = None
        if st.session_state.current_project_id:
            with db_transaction() as conn:
                current_project = get_project_by_id(conn, st.session_state.current_project_id)
                
        if not current_project:
            current_project = projects[0]
            st.session_state.current_project_id = current_project["id"]

        with st.sidebar:
            st.markdown("---")
            st.subheader("📁 Project Management")
            
            # Project selection dropdown
            selected_project_name = st.selectbox(
                "Select Project", 
                project_names, 
                index=project_names.index(current_project["name"])
            )
            
            if selected_project_name != current_project["name"]:
                st.session_state.current_project_id = project_map[selected_project_name]["id"]
                safe_rerun()

            # Edit project form in an expander
            with st.expander(f"Edit '{selected_project_name}'"):
                with st.form("edit_project_form"):
                    new_project_name = st.text_input("New Project Name", value=current_project["name"], key="edit_proj_name")
                    new_project_desc = st.text_area("Description", value=current_project["description"] or "", key="edit_proj_desc")
                    col_del, _ = st.columns([1,2])
                    update_button = st.form_submit_button("Update Project Details")
                    
                    if update_button:
                        if new_project_name and new_project_name != current_project["name"]:
                            with db_transaction() as conn:
                                try:
                                    c = conn.cursor()
                                    c.execute("UPDATE projects SET name=?, description=? WHERE id=?", 
                                              (new_project_name, new_project_desc, current_project["id"]))
                                    rename_project_move_media(current_project["name"], new_project_name, current_username)
                                    log_action(current_username, "rename_project", f"from '{current_project['name']}' to '{new_project_name}'")
                                    st.success("Project updated!")
                                    st.session_state.current_project_id = get_project_by_name(conn, user_id, new_project_name)["id"]
                                    safe_rerun()
                                except sqlite3.IntegrityError:
                                    st.error("A project with this name already exists.")
                                except Exception as e:
                                    st.error(f"Unable to update project: {e}")
                        elif new_project_name == current_project["name"] and new_project_desc != current_project["description"]:
                            with db_transaction() as conn:
                                try:
                                    c = conn.cursor()
                                    c.execute("UPDATE projects SET description=? WHERE id=?", (new_project_desc, current_project["id"]))
                                    log_action(current_username, "update_project_desc", current_project["name"])
                                    st.success("Project description updated!")
                                except Exception as e:
                                    st.error(f"Unable to update project: {e}")
                        else:
                            st.warning("No changes to update.")

            st.markdown("---")
            # Create new project form
            with st.expander("Create New Project"):
                with st.form("new_project_form_expanded"):
                    new_project_name = st.text_input("New Project Name", placeholder="e.g., Spring Campaign 2025", key="new_proj_name_exp")
                    new_project_description = st.text_area("Description (optional)", key="new_proj_desc_exp")
                    if st.form_submit_button("Create Project"):
                        if new_project_name:
                            with db_transaction() as conn:
                                try:
                                    pid = create_project(conn, user_id, new_project_name, new_project_description)
                                    st.session_state.current_project_id = pid
                                    log_action(current_username, "create_project", new_project_name)
                                    st.success(f"Project '{new_project_name}' created.")
                                    safe_rerun()
                                except sqlite3.IntegrityError:
                                    st.error("A project with this name already exists.")
                                except Exception as e:
                                    st.error(f"Unable to create project: {e}")
                        else:
                            st.error("Project name cannot be empty.")

        current = current_project["name"]
        project_id = current_project["id"]

        st.header(f"Project: {current}")
        st.subheader(current_project["description"] or "No description provided.")
        st.markdown("---")

        col_add, col_export, col_download = st.columns([1,1,1])

        with col_download:
            # Download All Photos for the project
            with db_transaction() as conn:
                cur = conn.cursor()
                cur.execute("SELECT photo_path FROM participants WHERE project_id=?", (project_id,))
                photo_paths = [row["photo_path"] for row in cur.fetchall() if row["photo_path"]]

            if photo_paths:
                with st.spinner("Preparing photos..."):
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for photo_path in photo_paths:
                            try:
                                zipf.write(photo_path, arcname=os.path.basename(photo_path))
                                # Also add the thumbnail if it exists
                                base, _ = os.path.splitext(photo_path)
                                thumb_path = f"{base}_thumb.jpg"
                                if os.path.exists(thumb_path):
                                    zipf.write(thumb_path, arcname=os.path.basename(thumb_path))
                            except Exception:
                                pass
                    
                    st.download_button(
                        label="💾 Download All Photos",
                        data=zip_buffer.getvalue(),
                        file_name=f"{_sanitize_for_path(current)}_photos.zip",
                        mime="application/zip"
                    )
            else:
                st.info("No photos in this project.")

        # Main participant display and creation area
        st.markdown("---")
        st.subheader("👤 Add New Participant")
        with st.form("new_participant_form", clear_on_submit=True):
            cols_main = st.columns(4)
            pnumber = cols_main[0].text_input("Number", placeholder="e.g., #01")
            pname = cols_main[1].text_input("Name", placeholder="Full Name")
            prole = cols_main[2].text_input("Role", placeholder="e.g., Actor")
            page = cols_main[3].text_input("Age", placeholder="e.g., 25")

            cols_details = st.columns(4)
            pagency = cols_details[0].text_input("Agency", placeholder="e.g., Talent Inc.")
            pheight = cols_details[1].text_input("Height", placeholder="e.g., 5'10\"")
            pwaist = cols_details[2].text_input("Waist", placeholder="e.g., 32")
            pdress = cols_details[3].text_input("Dress/Suit", placeholder="e.g., M / 40L")
            
            pavail = st.text_input("Next Availability", placeholder="e.g., Mon, Wed, Fri afternoons")
            
            p_photo_file = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
            
            submit_button = st.form_submit_button("Add Participant")
            if submit_button:
                if not pname:
                    st.error("Name is required.")
                else:
                    new_photo_path = None
                    if p_photo_file:
                        new_photo_path = save_photo_file(p_photo_file, current_username, current)

                    with db_transaction() as conn:
                        try:
                            conn.execute("""
                                INSERT INTO participants (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, pnumber, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, new_photo_path))
                            log_action(current_username, "add_participant", pname)
                            st.success("Participant added!")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to add participant: {e}")

        # Display Participants
        st.markdown("---")
        st.subheader("📋 Participants")
        with db_transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY name COLLATE NOCASE", (project_id,))
            participants = cur.fetchall()

        if not participants:
            st.info("No participants added yet.")
        else:
            if "editing_participant" not in st.session_state:
                st.session_state.editing_participant = None

            for p in participants:
                pid = p["id"]
                st.markdown('<div class="part-row">', unsafe_allow_html=True)
                
                # Left side card
                left, right = st.columns([1,1])
                with left:
                    st.markdown('<div class="participant-letterbox">', unsafe_allow_html=True)
                    img_path = p["photo_path"]
                    if img_path and os.path.exists(img_path):
                        thumb_path = thumb_path_for(img_path)
                        b64_image = image_b64_for_path(thumb_path)
                        if b64_image:
                            st.markdown(f'<img src="{b64_image}" class="photo" />', unsafe_allow_html=True)
                        else:
                            st.warning("Photo not found.")
                    else:
                        st.markdown('<div class="photo"></div>', unsafe_allow_html=True)

                    st.markdown(f'<div class="name">{safe_field(p,"name")}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="meta">{safe_field(p,"role") or "No role"}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="small">Agency: {safe_field(p,"agency")}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="small">Availability: {safe_field(p,"availability")}</div>', unsafe_allow_html=True)
                    
                    details = f"Age: {safe_field(p,'age')}, Height: {safe_field(p,'height')}, Waist: {safe_field(p,'waist')}, Dress/Suit: {safe_field(p,'dress_suit')}"
                    with st.expander("More Details"):
                        st.markdown(f'<div class="small">{details}</div>', unsafe_allow_html=True)

                    st.markdown('</div>', unsafe_allow_html=True)

                # Right side actions
                with right:
                    st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True) # Spacer
                    if st.session_state.editing_participant == pid:
                        st.subheader("✏️ Edit Participant")
                        with st.form(f"edit_form_{pid}"):
                            pid = st.session_state.editing_participant
                            with db_transaction() as conn:
                                p = conn.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                            
                            ecols1 = st.columns(2)
                            ecols2 = st.columns(2)
                            ecols3 = st.columns(2)

                            enumber = ecols1[0].text_input("Number", value=p["number"] or "", key=f"enumber_{pid}")
                            ename = ecols1[1].text_input("Name", value=p["name"] or "", key=f"ename_{pid}")
                            erole = ecols2[0].text_input("Role", value=p["role"] or "", key=f"erole_{pid}")
                            eage = ecols2[1].text_input("Age", value=p["age"] or "", key=f"eage_{pid}")
                            eagency = ecols3[0].text_input("Agency", value=p["agency"] or "", key=f"eagency_{pid}")
                            eheight = ecols3[1].text_input("Height", value=p["height"] or "", key=f"eheight_{pid}")
                            
                            ewaisd = st.text_input("Waist", value=p["waist"] or "", key=f"ewaist_{pid}")
                            edress = st.text_input("Dress/Suit", value=p["dress_suit"] or "", key=f"edress_{pid}")
                            eavail = st.text_input("Next Availability", value=p["availability"] or "", key=f'eavail_{pid}')
                            ephoto = st.file_uploader("Upload New Photo", type=["jpg","jpeg","png"], key=f"ephoto_{pid}")

                            save_edit = st.form_submit_button("Save Changes")
                            cancel_edit = st.form_submit_button("Cancel")
                            
                            if save_edit:
                                try:
                                    with db_transaction() as conn:
                                        new_photo_path = p["photo_path"]
                                        if ephoto:
                                            # Remove old photo before saving new one
                                            if p["photo_path"]:
                                                remove_media_file(p["photo_path"])
                                            new_photo_path = save_photo_file(ephoto, current_username, current)

                                        conn.execute("""
                                            UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=? WHERE id=?
                                        """, (enumber, ename, erole, eage, eagency, eheight, ewaisd, edress, eavail, new_photo_path, pid))
                                        log_action(current_username, "edit_participant", ename)
                                        st.success("Participant updated!")
                                        st.session_state.editing_participant = None
                                        safe_rerun()
                                except Exception as e:
                                    st.error(f"Unable to save participant edits: {e}")
                            if cancel_edit:
                                st.session_state.editing_participant = None
                                safe_rerun()
                    else:
                        edit_col, delete_col = st.columns(2)
                        if edit_col.button("✏️ Edit", key=f"edit_{pid}"):
                            st.session_state.editing_participant = pid
                            safe_rerun()

                        if delete_col.button("🗑️ Delete", key=f"del_{pid}"):
                            try:
                                with db_transaction() as conn:
                                    # Delete photo file before deleting DB record
                                    if p["photo_path"]:
                                        remove_media_file(p["photo_path"])
                                    conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                                    log_action(current_username, "delete_participant", p["name"] or "")
                                    st.warning("Participant deleted.")
                                    safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to delete participant: {e}")
                
                st.markdown('</div>', unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

        # ------------------------
        # Export to Word (fixed safe_field usage)
        # ------------------------
        with col_export:
            if st.button("Download as Word"):
                try:
                    with db_transaction() as conn:
                        c = conn.cursor()
                        c.execute("SELECT * FROM participants WHERE project_id=? ORDER BY name COLLATE NOCASE", (project_id,))
                        participants = c.fetchall()

                    doc = Document()
                    doc.add_heading(f"{current} Participants", 0)

                    for p in participants:
                        doc.add_heading(safe_field(p, "name"), level=1)
                        if p["photo_path"] and os.path.exists(p["photo_path"]):
                            try:
                                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_img:
                                    # Create a temporary thumbnail for Word export
                                    img = Image.open(p["photo_path"])
                                    img.thumbnail((400, 400))
                                    img.convert("RGB").save(tmp_img.name, format="JPEG", quality=75)
                                    doc.add_picture(tmp_img.name, width=Inches(3))
                                    os.unlink(tmp_img.name) # Clean up temp file
                            except UnidentifiedImageError:
                                st.warning(f"Could not process image for {p['name']}.")
                            except Exception:
                                pass
                        
                        doc.add_paragraph(f"Role: {safe_field(p, 'role')}")
                        doc.add_paragraph(f"Age: {safe_field(p, 'age')}")
                        doc.add_paragraph(f"Agency: {safe_field(p, 'agency')}")
                        doc.add_paragraph(f"Availability: {safe_field(p, 'availability')}")
                        doc.add_paragraph(f"Details: Height {safe_field(p, 'height')}, Waist {safe_field(p, 'waist')}, Dress/Suit {safe_field(p, 'dress_suit')}")
                        doc.add_page_break()

                    word_stream = io.BytesIO()
                    doc.save(word_stream)
                    word_stream.seek(0)
                    st.download_button(
                        label="Click to download Word file",
                        data=word_stream,
                        file_name=f"{_sanitize_for_path(current)}_participants.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                except Exception as e:
                    st.error(f"Unable to generate Word file: {e}")

        # ------------------------
        # Admin dashboard + backup & restore UI
        # ------------------------
        if role == "Admin":
            st.markdown("---")
            st.header("👑 Admin Dashboard")
            if st.button("🔄 Refresh Users"):
                safe_rerun()

            # Backup tools
            with st.expander("🗄️ Backups & Restore (Admin)", expanded=False):
                st.write("Create backups, download existing backups, or restore DB/media from uploads. **Always create a backup first.**")
                col1, col2, col3 = st.columns([2,2,3])
                if col1.button("Create DB Backup"):
                    bp = make_db_backup()
                    if bp: st.success(f"DB backup created: {bp}")
                    else: st.warning("No DB file found to back up.")
                
                if col2.button("Create Media Backup (zip)"):
                    mp = make_media_backup()
                    if mp: st.success(f"Media backup created: {mp}")
                    else: st.info("No media folder to back up.")
                
                if col3.button("Create Combined Backup (DB + media zip)"):
                    cb = make_combined_backup()
                    if cb: st.success(f"Combined backup created: {cb}")
                    else: st.error("Failed to create combined backup.")
                
                st.markdown("---")
                st.subheader("Existing Backups")
                backups = list_backups()
                if not backups:
                    st.info("No backups found.")
                else:
                    for fname in backups[:50]:
                        fp = os.path.join(BACKUPS_DIR, fname)
                        cols = st.columns([6,1,1])
                        cols[0].write(fname)
                        if cols[1].download_button("Download", data=download_file_bytes(fp), file_name=fname, mime="application/octet-stream"):
                            log_action(current_username, "download_backup", fname)
                            st.success(f"Downloaded {fname}")
                        
                        if cols[2].button("Delete", key=f"del_{fname}"):
                            try:
                                os.unlink(fp)
                                st.warning(f"{fname} deleted.")
                                log_action(current_username, "delete_backup", fname)
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to delete backup file: {e}")

                st.markdown("---")
                st.subheader("Restore from Upload")
                st.warning("⚠️ **DANGER ZONE** ⚠️ Restoring will overwrite your current data. Be sure you have a backup.")
                
                db_restore_file = st.file_uploader("Upload Database (.sqlite)", type=["sqlite", "db", "sqlite3"])
                if st.button("Restore Database"):
                    success, msg = restore_db_from_uploaded(db_restore_file)
                    if success:
                        st.success(msg)
                        log_action(current_username, "restore_db")
                    else:
                        st.error(msg)

                media_restore_file = st.file_uploader("Upload Media Backup (.zip)", type=["zip"])
                if st.button("Restore Media"):
                    success, msg = restore_media_from_uploaded(media_restore_file)
                    if success:
                        st.success(msg)
                        log_action(current_username, "restore_media")
                    else:
                        st.error(msg)
                
                combined_restore_file = st.file_uploader("Upload Combined Backup (.zip)", type=["zip"])
                if st.button("Restore Combined (DB + Media)"):
                    success, msg = restore_combined_from_uploaded(combined_restore_file)
                    if success:
                        st.success(msg)
                        log_action(current_username, "restore_combined")
                    else:
                        st.error(msg)

            # User management
            with st.expander("👤 User Management (Admin)", expanded=False):
                with db_transaction() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")
                    users_rows = cur.fetchall()
                
                st.subheader("Existing Users")
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
                    last_login = datetime.fromisoformat(u["last_login"]).strftime("%Y-%m-%d %H:%M") if u["last_login"] else "Never"
                    
                    if (uquery.lower() in uname.lower() or uquery.lower() in urole.lower()) and \
                       (urole_filter == "All" or urole_filter == urole):
                        with db_transaction() as conn:
                            num_projects = len(list_projects_for_user(conn, u["id"]))
                            
                        uact = st.columns([3,2,3,3,4])
                        uact[0].write(uname)
                        uact[1].write(urole)
                        uact[2].write(last_login)
                        uact[3].write(num_projects)
                        if uname != current_username:
                            if uact[4].button("Delete", key=f"del_user_{u['id']}"):
                                if st.warning(f"Are you sure you want to delete user '{uname}'? This is permanent."):
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
                        else:
                            uact[4].write("Cannot delete self")
                            
                st.markdown("---")
                st.subheader("Add New User")
                with st.form("add_user_form"):
                    nuser = st.text_input("Username")
                    npass = st.text_input("Password", type="password")
                    nrole = st.selectbox("Role", ["Casting Director", "Assistant", "Admin"], index=0)
                    if st.form_submit_button("Add User"):
                        if not nuser or not npass:
                            st.error("Username and password cannot be empty.")
                        else:
                            with db_transaction() as conn:
                                try:
                                    create_user(conn, nuser, hash_password(npass), nrole)
                                    log_action(current_username, "create_user", nuser)
                                    st.success(f"User '{nuser}' added.")
                                    safe_rerun()
                                except sqlite3.IntegrityError:
                                    st.error("User with this username already exists.")
                                except Exception as e:
                                    st.error(f"Unable to add user: {e}")

    st.sidebar.markdown("---")
    if st.sidebar.button("🔓 Logout"):
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.role = None
        st.session_state.user_id = None
        st.session_state.current_project_id = None
        safe_rerun()

else: # Login page
    st.header("Login to Sacha's Casting Manager")
    st.markdown("---")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_button = st.form_submit_button("Login")
        
        if login_button:
            with db_transaction() as conn:
                user = get_user_by_username(conn, username)
                if user and user["password"] == hash_password(password):
                    st.session_state.logged_in = True
                    st.session_state.username = user["username"]
                    st.session_state.role = user["role"]
                    st.session_state.user_id = user["id"]
                    log_action(user["username"], "login")
                    update_user_last_login(conn, user["id"])
                    
                    projects = list_projects_for_user(conn, user["id"])
                    if projects:
                        st.session_state.current_project_id = projects[0]["id"]

                    st.success(f"Welcome, {user['username']}!")
                    safe_rerun()
                else:
                    st.error("Invalid username or password.")
