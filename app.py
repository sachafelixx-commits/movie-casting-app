# sacha_casting_manager_with_hardcoded_admin.py
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

# ==================================
# Config
# ==================================
st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")

DB_FILE = "data.db"
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

# ==================================
# Inject UI CSS for letter-box participant cards (touch-friendly)
# ==================================
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
.participant-letterbox.photo {
  width: 100%;
  height: 220px;
  display:block;
  object-fit: cover;
  border-radius: 8px;
  background: #f6f6f6;
  margin-bottom: 8px;
}
.participant-letterbox.name {
  font-weight: 700;
  font-size: 1.05rem;
  margin-bottom: 6px;
}
.participant-letterbox.meta {
  color: rgba(0,0,0,0.6);
  font-size: 0.95rem;
  margin-bottom: 4px;
}
.participant-letterbox.small {
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
 .participant-letterbox.photo { height: 160px; }
}
@media (max-width: 600px) {
 .participant-letterbox { max-width: 100%; padding: 6px; }
 .participant-letterbox.photo { height: 140px; }
 .part-row { flex-direction: column; }
}

/* Buttons slightly larger for touch */
.stButton>button, button {
  padding:.55rem .9rem!important;
  font-size: 0.98rem!important;
}
</style>
""", unsafe_allow_html=True)

# ==================================
# Utilities
# ==================================
def _sanitize_for_path(s: str) -> str:
    """Sanitize a string to be used as a path component."""
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return re.sub(r"[^0-9A-Za-z\-_]+", "_", s)

def hash_password(password: str) -> str:
    """Hash a password using SHA256.
    Note: For production, consider a stronger, salt-based hash like bcrypt."""
    return hashlib.sha256(password.encode()).hexdigest()

def ensure_media_dir():
    """Ensure the media directory exists."""
    os.makedirs(MEDIA_DIR, exist_ok=True)

def looks_like_base64_image(s: str) -> bool:
    """Check if a string looks like a base64 encoded image."""
    if not isinstance(s, str):
        return False
    if len(s) < 120:
        return False
    if os.path.exists(s):
        return False
    # A simple regex check for common base64 chars
    if re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", s):
        return True
    return False

def safe_field(row_or_dict, key, default=""):
    """
    Safely get a field from sqlite3.Row or a dict-like object.
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

def safe_rerun():
    """Tries to re-run the Streamlit script, gracefully handling deprecations."""
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
    # As a last resort, toggle a session flag to force a re-execution
    st.session_state["_needs_refresh"] = not st.session_state.get("_needs_refresh", False)
    return

# ==================================
# DB connection caching (fast)
# ==================================
@st.cache_resource
def get_db_conn():
    """Returns a cached, long-lived DB connection."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute(f"PRAGMA synchronous = {PRAGMA_SYNCHRONOUS};")
        cur.execute("PRAGMA temp_store = MEMORY;")
    except Exception:
        pass
    return conn

@contextmanager
def db_transaction():
    """Provides a transactional context for DB operations."""
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

# ==================================
# Image helpers
# ==================================
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
    base, _ = os.path.splitext(photo_path)
    thumb = f"{base}_thumb.jpg"
    if os.path.exists(thumb):
        return thumb
    if os.path.exists(photo_path):
        return photo_path
    return None

def save_photo_file(uploaded_file, username: str, project_name: str, make_thumb=True, thumb_size=THUMB_SIZE) -> str:
    """Save an uploaded file to the media directory and create a thumbnail."""
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
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        
        # create thumbnail next to original (jpg)
        if make_thumb:
            try:
                buf = io.BytesIO(data)
                img = Image.open(buf)
                img.thumbnail(thumb_size)
                base_name = os.path.splitext(filename)[0]
                thumb_name = f"{base_name}_thumb.jpg"
                thumb_path = os.path.join(user_dir, thumb_name)
                img.convert("RGB").save(thumb_path, format="JPEG", quality=THUMB_QUALITY)
            except Exception:
                # ignore thumbnail errors
                pass
        return path.replace("\\", "/")
    except Exception:
        return None

def save_photo_bytes(bytes_data: bytes, username: str, project_name: str, ext_hint: str = ".jpg") -> str:
    """Save raw image bytes to the media directory and create a thumbnail."""
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
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        # create thumbnail
        try:
            buf2 = io.BytesIO(bytes_data)
            img = Image.open(buf2)
            img.thumbnail(THUMB_SIZE)
            base_name = os.path.splitext(filename)[0]
            thumb_name = f"{base_name}_thumb.jpg"
            thumb_path = os.path.join(user_dir, thumb_name)
            img.convert("RGB").save(thumb_path, format="JPEG", quality=THUMB_QUALITY)
        except Exception:
            pass
        return path.replace("\\", "/")
    except Exception:
        return None

def remove_media_file(path: str):
    """Safely remove a file and its associated thumbnail from the media directory."""
    try:
        if not path:
            return
        if isinstance(path, str) and os.path.exists(path):
            # ensure path is under MEDIA_DIR
            try:
                common = os.path.commonpath([os.path.abspath(path), os.path.abspath(MEDIA_DIR)])
            except Exception:
                common = ""
            if common != os.path.abspath(MEDIA_DIR):
                # safety: don't delete files outside media dir
                return
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

# ==================================
# SQLite helpers & schema management
# ==================================
def db_connect():
    """Returns a new DB connection, not from cache."""
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
    """Create DB and basic schema if missing."""
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

def log_action(user, action, details=""):
    """Insert a log row into logs table.
    Best-effort: quietly ignore on failure."""
    try:
        with db_transaction() as conn:
            conn.execute(
                "INSERT INTO logs (timestamp, user, action, details) VALUES (?,?,?,?)",
                (datetime.now().isoformat(), user, action, details)
            )
    except Exception:
        pass

# ==================================
# Migration from users.json (optional)
# ==================================
def migrate_from_json_if_needed():
    """Migrate data from an old users.json file to the SQLite DB if a marker file doesn't exist."""
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

    with st.spinner("Migrating data from users.json..."):
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
                
                # IMPORTANT: Admin backdoor from older versions removed for security
                if uname == "admin":
                    role = "Admin"
                
                try:
                    c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?,?,?,?)",
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
                            c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?,?,?,?)",
                                    (user_id, pname, desc, created_at))
                            project_id = c.lastrowid
                        except sqlite3.IntegrityError:
                            c.execute("SELECT id FROM projects WHERE user_id=? AND name=?", (user_id, pname))
                            prow = c.fetchone()
                            project_id = prow["id"] if prow else None
                        if project_id:
                            participants = pblock.get("participants") or []
                            if not isinstance(participants, (list, tuple)):
                                participants = []
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
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
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

# ==================================
# DB Operations
# ==================================
def get_user_by_username(conn, username):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    return c.fetchone()

def create_user(conn, username, password_hash, role="Casting Director"):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?,?,?,?)",
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
        WHERE p.user_id =?
        ORDER BY p.name COLLATE NOCASE
    """, (user_id,))
    return c.fetchall()

def create_project(conn, user_id, name, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?,?,?,?)",
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

def list_sessions_for_project(conn, project_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY date IS NULL, date, created_at", (project_id,))
    return cur.fetchall()

def create_session(conn, project_id, name, date=None, description=""):
    now = datetime.now().isoformat()
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (project_id, name, date, description, created_at) VALUES (?,?,?,?,?)",
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
    
def get_user_by_id(conn, user_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    return cur.fetchone()

def delete_user_data(conn, user_id, username):
    """Deletes all user data including projects, participants, sessions, and media files."""
    try:
        c = conn.cursor()
        
        # Get all project IDs for the user
        c.execute("SELECT id, name FROM projects WHERE user_id=?", (user_id,))
        projects_to_delete = c.fetchall()
        
        # Delete media for each project
        for project in projects_to_delete:
            delete_project_media(username, project["name"])
            
        # Delete participants, sessions, projects
        c.execute("DELETE FROM participants WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (user_id,))
        c.execute("DELETE FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (user_id,))
        c.execute("DELETE FROM projects WHERE user_id=?", (user_id,))
        
        # Delete the user
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        
        return True
    except Exception as e:
        print(f"Error deleting user data: {e}")
        return False
        
# ==================================
# App UI Functions
# ==================================

def show_login_signup():
    """Renders the login/signup UI."""
    st.title("🎬 Sacha's Casting Manager")
    choice = st.radio("Choose an option", ("Login", "Sign Up"), horizontal=True)

    if choice == "Login":
        username = st.text_input("Username", value=st.session_state.get("prefill_username", ""))
        if st.session_state.get("prefill_username"):
            st.session_state["prefill_username"] = ""
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")
        if login_btn:
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
            role = st.selectbox("Role", ("Casting Director", "Admin"))
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

def show_participant_kiosk(user_id, current_username, active_project_name):
    """Renders the simplified UI for participant check-ins."""
    st.title("👋 Casting Check-In")
    st.caption("Fill in your details. Submissions go to the active project.")
    st.info(f"Submitting to project: **{active_project_name}**")
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
        photo = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png"])
        submitted = st.form_submit_button("Submit")
    if submitted:
        with st.spinner("Submitting..."):
            try:
                with db_transaction() as conn:
                    proj = get_project_by_name(conn, user_id, active_project_name)
                    if not proj:
                        pid = create_project(conn, user_id, active_project_name, "")
                    else:
                        pid = proj["id"]
                    photo_path = save_photo_file(photo, current_username, active_project_name) if photo else None
                    conn.execute("""
                        INSERT INTO participants (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (pid, number, name, role_in, age, agency, height, waist, dress_suit, availability, photo_path))
                    log_action(current_username, "participant_checkin", name)
                st.success("Submission successful!")
                time.sleep(0.5)
                safe_rerun()
            except Exception as e:
                st.error(f"Error submitting: {e}")

def project_manager_ui(user_id, current_username):
    """Renders the project management section."""
    st.header("📁 Project Manager")
    
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
                            st.session_state["current_project_name"] = p_name
                            safe_rerun()
                except Exception as e:
                    st.error(f"Unable to create project: {e}")

    # Project List
    conn_read = get_db_conn()
    proj_rows = list_projects_with_counts(conn_read, user_id)
    # Project items as (name, desc, created_at, count)
    proj_items = [(r["name"], r["description"], r["created_at"], r["participant_count"]) for r in proj_rows]

    pm_col1, pm_col2 = st.columns(2)
    with pm_col1:
        query = st.text_input("Search projects by name or description")
    with pm_col2:
        sort_opt = st.selectbox("Sort by", ["Name A→Z", "Newest", "Oldest", "Most Participants", "Fewest Participants"], index=0)

    if query:
        q = query.lower().strip()
        proj_items = [x for x in proj_items if q in (x[0] or "").lower() or q in (x[1] or "").lower()]
    
    if sort_opt == "Name A→Z":
        proj_items.sort(key=lambda x: (x[0] or "").lower())
    elif sort_opt == "Newest":
        proj_items.sort(key=lambda x: (x[2] or ""), reverse=True)
    elif sort_opt == "Oldest":
        proj_items.sort(key=lambda x: (x[2] or ""))
    elif sort_opt == "Most Participants":
        proj_items.sort(key=lambda x: int(x[3] or 0), reverse=True)
    elif sort_opt == "Fewest Participants":
        proj_items.sort(key=lambda x: int(x[3] or 0))

    hdr = st.columns(5)
    hdr[0].markdown("**Project**")
    hdr[1].markdown("**Description**")
    hdr[2].markdown("**Created**")
    hdr[3].markdown("**Participants**")
    hdr[4].markdown("**Actions**")

    for name, desc, created, count in proj_items:
        is_active = (name == st.session_state.get("current_project_name"))
        cols = st.columns(5)
        cols[0].markdown(f"{'🟢 ' if is_active else ''}**{name}**")
        cols[1].markdown(desc or "—")
        cols[2].markdown((created or "").split("T")[0] if created else "—")
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
        
        # Inline edit form
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
        

        # Delete confirmation form
        if st.session_state.get("confirm_delete_project") == name:
            st.warning(f"Type project name **{name}** to confirm deletion. This cannot be undone.")
            with st.form(f"confirm_delete_{name}"):
                confirm_text = st.text_input("Confirm name")
                d1,d2 = st.columns(2)
                do_delete = d1.form_submit_button("Delete Permanently")
                cancel_delete = d2.form_submit_button("Cancel")
                if do_delete:
                    if confirm_text == name:
                        with st.spinner("Deleting project and all media..."):
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
                                        time.sleep(0.5)
                                        safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to delete project: {e}")
                    else:
                        st.error("Project name mismatch. Not deleted.")
                if cancel_delete:
                    st.session_state["confirm_delete_project"] = None
                    safe_rerun()

def participant_manager_ui(project_id, user_id, current_username, current_project_name):
    """Renders the participant management section."""
    st.header("👥 Participants")
    
    # Bulk actions and filtering
    col1, col2 = st.columns(2)
    with col1:
        p_query = st.text_input("Search participants by name, role, etc.")
    with col2:
        session_opts = ["All Participants", "Unassigned"]
        with db_connect() as conn:
            sess_rows = list_sessions_for_project(conn, project_id)
            for s in sess_rows:
                session_opts.append(s["name"])
        view_mode_name = st.selectbox("Filter by Session", options=session_opts, index=0)
        
        if st.button("Clear Bulk Selection"):
            st.session_state["bulk_selection"] = set()
            safe_rerun()

    # Get participants based on filter
    conn_read = get_db_conn()
    cur = conn_read.cursor()
    sql = "SELECT * FROM participants WHERE project_id=?"
    params = [project_id]
    if view_mode_name == "All Participants":
        st.session_state["view_mode"] = "all"
        st.session_state["view_session_id"] = None
    elif view_mode_name == "Unassigned":
        st.session_state["view_mode"] = "unassigned"
        sql += " AND session_id IS NULL"
    else:
        st.session_state["view_mode"] = "session"
        with db_connect() as conn:
            sess_row = conn.execute("SELECT id FROM sessions WHERE name=? AND project_id=?", (view_mode_name, project_id)).fetchone()
            if sess_row:
                st.session_state["view_session_id"] = sess_row["id"]
                sql += " AND session_id=?"
                params.append(st.session_state["view_session_id"])
            else:
                st.session_state["view_mode"] = "all"
                st.session_state["view_session_id"] = None

    sql += " ORDER BY name COLLATE NOCASE"
    cur.execute(sql, params)
    participant_rows = cur.fetchall()

    if p_query:
        q = p_query.lower().strip()
        participant_rows = [r for r in participant_rows if q in safe_field(r, "name").lower() or q in safe_field(r, "role").lower() or q in safe_field(r, "number").lower()]

    # Bulk actions UI
    if st.session_state.get("bulk_selection"):
        st.info(f"{len(st.session_state['bulk_selection'])} participants selected.")
        bulk_col1, bulk_col2, bulk_col3, bulk_col4 = st.columns(4)
        with bulk_col1:
            session_names = ["(Select Session)"] + [s["name"] for s in sess_rows]
            bulk_target_session_name = st.selectbox("Assign to Session", options=session_names)
        with bulk_col2:
            if st.button("Assign Selected"):
                if bulk_target_session_name != "(Select Session)":
                    with db_transaction() as conn:
                        sess_row = conn.execute("SELECT id FROM sessions WHERE name=? AND project_id=?", (bulk_target_session_name, project_id)).fetchone()
                        if sess_row:
                            assign_participants_to_session(conn, list(st.session_state["bulk_selection"]), sess_row["id"])
                            num_assigned = len(st.session_state["bulk_selection"])
                            st.session_state["bulk_selection"] = set()
                            st.success(f"{num_assigned} participants assigned.")
                            log_action(current_username, "bulk_assign", f"{num_assigned} to {bulk_target_session_name}")
                            safe_rerun()
                else:
                    st.warning("Please select a session.")
        with bulk_col3:
            if st.button("Unassign Selected"):
                with db_transaction() as conn:
                    unassign_participants_from_session(conn, list(st.session_state["bulk_selection"]))
                num_unassigned = len(st.session_state["bulk_selection"])
                st.session_state["bulk_selection"] = set()
                st.success("Participants unassigned.")
                log_action(current_username, "bulk_unassign", f"{num_unassigned} unassigned")
                safe_rerun()

    # Participant list with photo cards
    st.markdown("---")
    if not participant_rows:
        st.info("No participants found in this project or session.")
    else:
        for r in participant_rows:
            photo_path = r["photo_path"]
            thumb = thumb_path_for(photo_path)
            b64_img = image_b64_for_path(thumb) if thumb else None
            is_selected = r["id"] in st.session_state.get("bulk_selection", set())
            
            with st.container():
                col_sel, col_content, col_actions = st.columns([1, 4, 2])
                with col_sel:
                    checkbox_state = st.checkbox("Select", value=is_selected, key=f"bulk_select_{r['id']}")
                    if checkbox_state:
                        st.session_state.setdefault("bulk_selection", set()).add(r["id"])
                    elif not checkbox_state and is_selected:
                        st.session_state.setdefault("bulk_selection", set()).discard(r["id"])
                        safe_rerun()

                with col_content:
                    st.markdown(f"**{safe_field(r,'name')}**", unsafe_allow_html=True)
                    if b64_img:
                        st.image(b64_img, caption=f"Photo of {safe_field(r, 'name')}", width=300)
                    st.markdown(f"**Number**: {safe_field(r, 'number')}")
                    st.markdown(f"**Role**: {safe_field(r, 'role')}")
                    st.markdown(f"**Age**: {safe_field(r, 'age')}")
                    st.markdown(f"**Agency**: {safe_field(r, 'agency')}")
                    
                with col_actions:
                    edit_btn = st.button("Edit", key=f"edit_part_{r['id']}")
                    delete_btn = st.button("Delete", key=f"delete_part_{r['id']}")
                    export_btn = st.button("Export to Word", key=f"export_part_{r['id']}")
                    
                    if edit_btn:
                        st.session_state["editing_participant_id"] = r["id"]
                        safe_rerun()
                    if delete_btn:
                        st.session_state["confirm_delete_participant_id"] = r["id"]
                        safe_rerun()
                    if export_btn:
                        export_participants_to_word([r], current_username, current_project_name)

            if st.session_state.get("editing_participant_id") == r['id']:
                with st.form(f"edit_participant_form_{r['id']}"):
                    new_number = st.text_input("Number", value=safe_field(r, 'number'))
                    new_name = st.text_input("Name", value=safe_field(r, 'name'))
                    new_role = st.text_input("Role", value=safe_field(r, 'role'))
                    new_age = st.text_input("Age", value=safe_field(r, 'age'))
                    new_agency = st.text_input("Agency", value=safe_field(r, 'agency'))
                    new_height = st.text_input("Height", value=safe_field(r, 'height'))
                    new_waist = st.text_input("Waist", value=safe_field(r, 'waist'))
                    new_dress_suit = st.text_input("Dress/Suit", value=safe_field(r, 'dress_suit'))
                    new_availability = st.text_input("Next Availability", value=safe_field(r, 'availability'))
                    update_btn = st.form_submit_button("Update Participant")
                    cancel_btn = st.form_submit_button("Cancel")
                    if update_btn:
                        with st.spinner("Updating..."):
                            try:
                                with db_transaction() as conn:
                                    conn.execute("""
                                        UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?
                                        WHERE id=?
                                    """, (new_number, new_name, new_role, new_age, new_agency, new_height, new_waist, new_dress_suit, new_availability, r["id"]))
                                    log_action(current_username, "edit_participant", new_name)
                                st.success("Participant updated.")
                                st.session_state["editing_participant_id"] = None
                                time.sleep(0.5)
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Failed to update participant: {e}")
                    if cancel_btn:
                        st.session_state["editing_participant_id"] = None
                        safe_rerun()
          
            if st.session_state.get("confirm_delete_participant_id") == r['id']:
                st.warning(f"Are you sure you want to delete {safe_field(r,'name')}? This cannot be undone.")
                c1, c2 = st.columns(2)
                if c1.button("Confirm Delete", key=f"confirm_del_part_{r['id']}"):
                    with st.spinner("Deleting..."):
                        try:
                            with db_transaction() as conn:
                                remove_media_file(r["photo_path"])
                                conn.execute("DELETE FROM participants WHERE id=?", (r["id"],))
                                log_action(current_username, "delete_participant", safe_field(r, 'name'))
                            st.success(f"Participant {safe_field(r,'name')} deleted.")
                            st.session_state["confirm_delete_participant_id"] = None
                            time.sleep(0.5)
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Failed to delete participant: {e}")
                if c2.button("Cancel", key=f"cancel_del_part_{r['id']}"):
                    st.session_state["confirm_delete_participant_id"] = None
                    safe_rerun()

def session_manager_ui(project_id, current_username):
    """Renders the session management section."""
    st.header("🗓️ Sessions")
    
    # Create session
    with st.expander("➕ Create New Session", expanded=False):
        with st.form("new_session_form"):
            s_name = st.text_input("Session Name")
            s_date = st.date_input("Session Date (optional)", value=None)
            s_desc = st.text_area("Description (optional)", height=80)
            create_sess_btn = st.form_submit_button("Create Session")
        if create_sess_btn:
            if not s_name:
                st.error("Session name is required.")
            else:
                try:
                    with db_transaction() as conn:
                        existing = conn.execute("SELECT id FROM sessions WHERE name=? AND project_id=?", (s_name, project_id)).fetchone()
                        if existing:
                            st.error("Session with this name already exists.")
                        else:
                            create_session(conn, project_id, s_name, s_date.isoformat() if s_date else None, s_desc or "")
                            log_action(current_username, "create_session", s_name)
                            st.success(f"Session '{s_name}' created.")
                            safe_rerun()
                except Exception as e:
                    st.error(f"Unable to create session: {e}")

    # Session list
    conn_read = get_db_conn()
    sess_rows = list_sessions_for_project(conn_read, project_id)
    
    sess_col1, sess_col2 = st.columns(2)
    with sess_col1:
        sess_query = st.text_input("Search sessions by name or description", key="sess_query")
    with sess_col2:
        sess_sort = st.selectbox("Sort sessions", ("Name", "Newest", "Oldest", "Date"), index=0, key="sess_sort")

    sess_items = [(r["id"], r["name"], r["date"], r["description"], r["created_at"]) for r in sess_rows]

    if sess_query:
        q = sess_query.lower().strip()
        sess_items = [x for x in sess_items if q in (x[1] or "").lower() or q in (x[3] or "").lower()]

    if sess_sort == "Name":
        sess_items.sort(key=lambda x: (x[1] or "").lower())
    elif sess_sort == "Newest":
        sess_items.sort(key=lambda x: (x[4] or ""), reverse=True)
    elif sess_sort == "Oldest":
        sess_items.sort(key=lambda x: (x[4] or ""))
    elif sess_sort == "Date":
        # Sort by date, with NULL dates at the end
        def date_sort_key(item):
            dt_str = item[2]
            if not dt_str:
                return (1, "")
            return (0, dt_str)
        sess_items.sort(key=date_sort_key)

    st.markdown("---")
    
    if not sess_items:
        st.info("No sessions found for this project.")
    else:
        for sess_id, sess_name, sess_date, sess_desc, sess_created in sess_items:
            with st.container():
                s_cols = st.columns(4)
                s_cols[0].markdown(f"**{sess_name}**")
                s_cols[1].markdown(sess_desc or "—")
                s_cols[2].markdown(sess_date.split("T")[0] if sess_date else "—")
                
                a1, a2, a3 = s_cols[3].columns([1,1,1])
                if a1.button("View", key=f"view_sess_{sess_id}"):
                    st.session_state["view_mode"] = "session"
                    st.session_state["view_session_id"] = sess_id
                    st.session_state["view_session_name"] = sess_name
                    safe_rerun()
                if a2.button("Export", key=f"export_sess_{sess_id}"):
                    conn_read = get_db_conn()
                    cur = conn_read.cursor()
                    cur.execute("SELECT * FROM participants WHERE session_id=?", (sess_id,))
                    participants = cur.fetchall()
                    if participants:
                        export_participants_to_word(participants, current_username, st.session_state.get("current_project_name", ""))
                    else:
                        st.info("No participants in this session to export.")
                if a3.button("Delete", key=f"del_sess_{sess_id}"):
                    st.session_state["confirm_delete_session_id"] = sess_id
                    st.session_state["confirm_delete_session_name"] = sess_name
                    safe_rerun()
    
            
            if st.session_state.get("confirm_delete_session_id") == sess_id:
                st.warning(f"Are you sure you want to delete the session **{sess_name}**?")
                c1, c2 = st.columns(2)
                if c1.button("Confirm Delete", key=f"conf_del_sess_{sess_id}"):
                    with st.spinner("Deleting session..."):
                        try:
                            with db_transaction() as conn:
                                # unassign participants from this session
                                conn.execute("UPDATE participants SET session_id=NULL WHERE session_id=?", (sess_id,))
                                delete_session(conn, sess_id)
                                log_action(current_username, "delete_session", sess_name)
                            st.success("✅ Session deleted.")
                            st.session_state["confirm_delete_session_id"] = None
                            time.sleep(0.5)
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Failed to delete session: {e}")
                if c2.button("Cancel", key=f"cancel_del_sess_{sess_id}"):
                    st.session_state["confirm_delete_session_id"] = None
                    safe_rerun()

def admin_dashboard_ui():
    """Renders the admin dashboard."""
    st.header("👑 Admin Dashboard")
    st.markdown("---")
    st.subheader("Manage Users")
    
    conn_read = get_db_conn()
    users = conn_read.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
    
    for user_row in users:
        uname = user_row["username"]
        role = user_row["role"]
        
        cols = st.columns(3)
        cols[0].markdown(f"**{uname}**")
        cols[1].markdown(role)
        
        if uname != st.session_state.get("current_user"):
            if cols[2].button("Delete User", key=f"delete_user_{uname}"):
                st.session_state["confirm_delete_user"] = uname
                safe_rerun()
    
    if st.session_state.get("confirm_delete_user"):
        uname = st.session_state["confirm_delete_user"]
        st.warning(f"Are you sure you want to delete user **{uname}**? This will delete all of their projects, participants, and media. This cannot be undone.")
        c1, c2 = st.columns(2)
        if c1.button("Confirm Delete User", key="final_del_user"):
            with st.spinner("Deleting user and all data..."):
                try:
                    with db_transaction() as conn:
                        user_to_delete = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
                        if user_to_delete and delete_user_data(conn, user_to_delete["id"], uname):
                            log_action(st.session_state.get("current_user", "unknown"), "delete_user", uname)
                        
                    st.success(f"User {uname} deleted.")
                    st.session_state["confirm_delete_user"] = None
                    time.sleep(0.5)
                    safe_rerun()
                except Exception as e:
                    st.error(f"Unable to delete user: {e}")
        if c2.button("Cancel", key="cancel_del_user"):
            st.session_state["confirm_delete_user"] = None
            safe_rerun()
    
def export_participants_to_word(participants, current_username, current_project_name):
    """Exports a list of participants to a Word document and provides a download button."""
    try:
        doc = Document()
        doc.add_heading(f"Casting Report for '{current_project_name}'", 0)
        doc.add_paragraph(f"Generated by {current_username} on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        for p in participants:
            doc.add_heading(safe_field(p, "name"), level=1)
            
            # Add photo if available
            photo_path = safe_field(p, "photo_path")
            if photo_path and os.path.exists(photo_path):
                try:
                    doc.add_picture(photo_path, width=Inches(3))
                except UnidentifiedImageError:
                    doc.add_paragraph("[Photo is not a valid image format]")
                except Exception:
                    doc.add_paragraph("[Photo could not be embedded]")
            
            # Add participant details in a table
            table = doc.add_table(rows=1, cols=2)
            table.style = 'Table Grid'
            hdr_cells = table.rows[0].cells
            hdr_cells[0].text = 'Field'
            hdr_cells[1].text = 'Value'
            
            fields = {
                "Number": safe_field(p, 'number'),
                "Role": safe_field(p, 'role'),
                "Age": safe_field(p, 'age'),
                "Agency": safe_field(p, 'agency'),
                "Height": safe_field(p, 'height'),
                "Waist": safe_field(p, 'waist'),
                "Dress/Suit": safe_field(p, 'dress_suit'),
                "Availability": safe_field(p, 'availability')
            }
            
            for field, value in fields.items():
                if value:
                    row_cells = table.add_row().cells
                    row_cells[0].text = field
                    row_cells[1].text = str(value)
            
            doc.add_page_break()
            
        # Save to a temporary in-memory buffer
        bio = io.BytesIO()
        doc.save(bio)
        bio.seek(0)
        
        # Provide download link
        st.download_button(
            label="Download Word Document",
            data=bio.getvalue(),
            file_name=f"Casting_Report_{datetime.now().strftime('%Y-%m-%d')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        st.success("Word document created and ready for download.")
        log_action(current_username, "export_to_word", f"Exported {len(participants)} participants.")
        
    except Exception as e:
        st.error(f"Failed to generate Word document: {e}")
    
# ==================================
# App Entry Point
# ==================================
def main():
    """Main function to run the Streamlit app."""
    
    # Initialize DB and run migration once
    init_db()
    migrate_from_json_if_needed()

    # --- Ensure default admin exists (username: admin, password: supersecret) ---
    try:
        with db_transaction() as conn:
            existing_admin = get_user_by_username(conn, "admin")
            if not existing_admin:
                create_user(conn, "admin", hash_password("supersecret"), role="Admin")
                log_action("system", "create_admin_auto", "Created default admin account")
    except Exception:
        # best-effort; don't crash startup if logging fails
        pass
    # -------------------------------------------------------------------------

    # Initialize session state variables
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
    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "all"
    if "view_session_id" not in st.session_state:
        st.session_state["view_session_id"] = None
    if "editing_participant_id" not in st.session_state:
        st.session_state["editing_participant_id"] = None
    if "confirm_delete_participant_id" not in st.session_state:
        st.session_state["confirm_delete_participant_id"] = None
    if "confirm_delete_session_id" not in st.session_state:
        st.session_state["confirm_delete_session_id"] = None
    if "confirm_delete_user" not in st.session_state:
        st.session_state["confirm_delete_user"] = None

    # If not logged in, show login/signup (no one-time admin setup page)
    if not st.session_state["logged_in"]:
        show_login_signup()
    else:
        current_username = st.session_state["current_user"]
        
        try:
            conn_temp = db_connect()
            user_row = get_user_by_username(conn_temp, current_username)
            conn_temp.close()
        except Exception:
            user_row = None
        
        if not user_row:
            st.error("User not found. Please log in again.")
            st.session_state["logged_in"] = False
            st.session_state["current_user"] = None
            safe_rerun()
            return
            
        user_id = user_row["id"]
        role = user_row["role"] or "Casting Director"
        
        # Sidebar menu
        st.sidebar.title("Menu")
        st.sidebar.write(f"Logged in as **{current_username}** ({role})")
        if st.sidebar.button("Logout"):
            st.session_state["logged_in"] = False
            st.session_state["current_user"] = None
            st.session_state["current_project_name"] = None
            safe_rerun()
        
        # Get active project (create default if needed)
        conn_read = get_db_conn()
        proj_rows = list_projects_with_counts(conn_read, user_id)
        if not proj_rows:
            with db_transaction() as conn:
                create_project(conn, user_id, DEFAULT_PROJECT_NAME, "")
            conn_read = get_db_conn()
            proj_rows = list_projects_with_counts(conn_read, user_id)
        
        project_names = [r["name"] for r in proj_rows]
        if st.session_state.get("current_project_name") not in project_names:
            st.session_state["current_project_name"] = project_names[0] if project_names else DEFAULT_PROJECT_NAME
        
        active_project_name = st.session_state["current_project_name"]
        
        st.sidebar.markdown("---")
        st.sidebar.subheader("Active Project")
        st.sidebar.write(f"**{active_project_name}**")
        
        st.sidebar.markdown("---")
        st.session_state["participant_mode"] = st.sidebar.checkbox("Enable Kiosk Mode", value=st.session_state.get("participant_mode", False))

        if st.session_state["participant_mode"]:
            show_participant_kiosk(user_id, current_username, active_project_name)
        else:
            st.title("🎬 Sacha's Casting Manager")
            
            tab_labels = ["Projects", "Participants", "Sessions", "Export"]
            if role == "Admin":
                tab_labels.append("Admin")
            tabs = st.tabs(tab_labels)
            
            with tabs[0]:
                project_manager_ui(user_id, current_username)
            
            # Re-fetch project ID to ensure it's up to date after potential creation/rename
            with db_connect() as conn:
                proj = get_project_by_name(conn, user_id, active_project_name)
            if not proj:
                st.error("Active project not found. Please select or create one.")
                return
            project_id = proj["id"]
            
            with tabs[1]:
                participant_manager_ui(project_id, user_id, current_username, active_project_name)
            
            with tabs[2]:
                session_manager_ui(project_id, current_username)

            with tabs[3]:
                st.header("📄 Export to Word")
                with st.container():
                    export_all = st.button("Export All Participants in Project")
                    export_session = st.button("Export Participants from Current Session View")
                    if export_all:
                        conn_read = get_db_conn()
                        cur = conn_read.cursor()
                        cur.execute("SELECT * FROM participants WHERE project_id=?", (project_id,))
                        participants = cur.fetchall()
                        if participants:
                            export_participants_to_word(participants, current_username, active_project_name)
                        else:
                            st.info("No participants to export.")
                    if export_session:
                        view_session_id = st.session_state.get("view_session_id")
                        if view_session_id:
                            conn_read = get_db_conn()
                            cur = conn_read.cursor()
                            cur.execute("SELECT * FROM participants WHERE session_id=?", (view_session_id,))
                            participants = cur.fetchall()
                            if participants:
                                export_participants_to_word(participants, current_username, active_project_name)
                            else:
                                st.info("No participants in the current session view to export.")
                        else:
                            st.info("Please select a session to filter participants before exporting.")

            if role == "Admin":
                with tabs[-1]:
                    admin_dashboard_ui()

if __name__ == "__main__":
    main()
