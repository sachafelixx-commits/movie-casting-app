# sachas_casting_manager_with_sessions.py
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
# Minimal CSS (participant letterbox + spacing fixes)
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
  color: #000 !important;
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
# Cached DB connection
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
# Image helpers (thumbnails + b64 caching)
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
# Save / thumbnail creation
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
# SQLite helpers + migration (ensure sessions tables exist)
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
    # create db if missing and ensure core tables exist. Use IF NOT EXISTS for additive migrations.
    with db_transaction() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                last_login TEXT
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        c.execute("""
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
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        """)
        # sessions and join table
        c.execute("""
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS session_participants (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                participant_id INTEGER NOT NULL,
                added_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (participant_id) REFERENCES participants(id)
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                user TEXT,
                action TEXT,
                details TEXT
            );
        """)
        # indices
        c.execute("CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_participants_project ON participants(project_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_session ON session_participants(session_id);")
        conn.commit()

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
# Migration from users.json (unchanged logic)
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

# Initialize DB + migrate once
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

# ================
# Sessions Helpers
# ================
def list_sessions_for_project(conn, project_id):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY date, name COLLATE NOCASE", (project_id,))
    return c.fetchall()

def create_session(conn, project_id, name, date_str=None, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO sessions (project_id, name, date, description, created_at) VALUES (?, ?, ?, ?, ?)",
              (project_id, name, date_str, description, now))
    return c.lastrowid

def get_session_by_id(conn, session_id):
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    return c.fetchone()

def update_session(conn, session_id, name, date_str, description):
    c = conn.cursor()
    c.execute("UPDATE sessions SET name=?, date=?, description=? WHERE id=?", (name, date_str, description, session_id))

def delete_session(conn, session_id):
    c = conn.cursor()
    # delete join rows first
    c.execute("DELETE FROM session_participants WHERE session_id=?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

def add_participant_to_session(conn, session_id, participant_id):
    c = conn.cursor()
    now = datetime.now().isoformat()
    # avoid duplicates
    c.execute("SELECT id FROM session_participants WHERE session_id=? AND participant_id=?", (session_id, participant_id))
    if c.fetchone():
        return None
    c.execute("INSERT INTO session_participants (session_id, participant_id, added_at) VALUES (?, ?, ?)",
              (session_id, participant_id, now))
    return c.lastrowid

def remove_participant_from_session(conn, session_id, participant_id):
    c = conn.cursor()
    c.execute("DELETE FROM session_participants WHERE session_id=? AND participant_id=?", (session_id, participant_id))

def participants_in_session(conn, session_id):
    c = conn.cursor()
    c.execute("""
        SELECT p.* FROM participants p
        JOIN session_participants sp ON sp.participant_id = p.id
        WHERE sp.session_id = ?
        ORDER BY p.id
    """, (session_id,))
    return c.fetchall()

def sessions_for_participant(conn, participant_id):
    c = conn.cursor()
    c.execute("""
        SELECT s.* FROM sessions s
        JOIN session_participants sp ON sp.session_id = s.id
        WHERE sp.participant_id = ?
        ORDER BY s.date, s.name
    """, (participant_id,))
    return c.fetchall()

def bulk_move_copy_participants(conn, participant_ids, target_session_id, action="move"):
    """
    action in {"move", "copy"}.
    If move: remove participant from all other sessions in same project then add to target.
    If copy: just add to target (if not already present).
    """
    c = conn.cursor()
    target_session = get_session_by_id(conn, target_session_id)
    if not target_session:
        raise ValueError("Target session not found")
    proj_id = target_session["project_id"]
    now = datetime.now().isoformat()
    results = {"added":0,"skipped":0,"removed":0}
    for pid in participant_ids:
        if action == "move":
            # remove from other sessions in same project for this participant
            # find sessions for this participant under proj_id
            c.execute("""
                SELECT sp.id, sp.session_id FROM session_participants sp
                JOIN sessions s ON s.id = sp.session_id
                WHERE sp.participant_id=? AND s.project_id=?
            """, (pid, proj_id))
            rows = c.fetchall()
            for r in rows:
                # if already in target_session, skip removal for that id
                if r["session_id"] != target_session_id:
                    c.execute("DELETE FROM session_participants WHERE id=?", (r["id"],))
                    results["removed"] += 1
            # add to target if not exists
            c.execute("SELECT id FROM session_participants WHERE session_id=? AND participant_id=?", (target_session_id, pid))
            if not c.fetchone():
                c.execute("INSERT INTO session_participants (session_id, participant_id, added_at) VALUES (?, ?, ?)", (target_session_id, pid, now))
                results["added"] += 1
            else:
                results["skipped"] += 1
        else: # copy
            c.execute("SELECT id FROM session_participants WHERE session_id=? AND participant_id=?", (target_session_id, pid))
            if not c.fetchone():
                c.execute("INSERT INTO session_participants (session_id, participant_id, added_at) VALUES (?, ?, ?)", (target_session_id, pid, now))
                results["added"] += 1
            else:
                results["skipped"] += 1
    return results

# ========================
# UI: Auth + state init
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
if "viewing_session_id" not in st.session_state:
    st.session_state["viewing_session_id"] = None  # None means "view all participants"
if "last_action_message" not in st.session_state:
    st.session_state["last_action_message"] = ""

# AUTH UI
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
        st.session_state["viewing_session_id"] = None
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

    # Participant Kiosk
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
                                    st.session_state["current_project_name"] = p_name
                        except Exception as e:
                            st.error(f"Unable to create project: {e}")

        # fetch projects and counts (fresh)
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

        # =========================
        # SESSIONS manager (separate section)
        # =========================
        st.header("🗂 Sessions")
        current = st.session_state["current_project_name"]
        with db_connect() as conn:
            proj = get_project_by_name(conn, user_id, current)
        if not proj:
            with db_transaction() as conn:
                create_project(conn, user_id, current, "")
            with db_connect() as conn:
                proj = get_project_by_name(conn, user_id, current)
        project_id = proj["id"]

        # Create session form
        with st.expander("➕ Create New Session", expanded=False):
            with st.form("new_session_form"):
                s_name = st.text_input("Session Name")
                s_date = st.date_input("Session Date", value=date.today())
                s_desc = st.text_area("Description", height=80)
                s_create = st.form_submit_button("Create Session")
                if s_create:
                    if not s_name:
                        st.error("Provide a session name")
                    else:
                        try:
                            with db_transaction() as conn:
                                create_session(conn, project_id, s_name, s_date.isoformat(), s_desc or "")
                                log_action(current_username, "create_session", f"{current} -> {s_name}")
                            st.success(f"Session '{s_name}' created.")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to create session: {e}")

        # List sessions
        with db_connect() as conn:
            sessions = list_sessions_for_project(conn, project_id)

        if not sessions:
            st.info("No sessions yet for this project.")
        else:
            # sessions header and quick controls
            ses_cols = st.columns([3,2,3,2])
            ses_cols[0].markdown("**Session**")
            ses_cols[1].markdown("**Date**")
            ses_cols[2].markdown("**Description**")
            ses_cols[3].markdown("**Actions**")
            for s in sessions:
                s_id = s["id"]
                cols = st.columns([3,2,3,2])
                is_viewing = (st.session_state.get("viewing_session_id") == s_id)
                view_label = "Viewing" if is_viewing else "View"
                cols[0].markdown(f"{'🟢 ' if is_viewing else ''}**{s['name']}**")
                cols[1].markdown((s["date"] or "").split("T")[0] if s["date"] else "—")
                cols[2].markdown(s["description"] or "—")
                c1, c2 = cols[3].columns([1,1])
                if c1.button(view_label, key=f"view_session_{s_id}"):
                    st.session_state["viewing_session_id"] = s_id
                    safe_rerun()
                if c2.button("Edit", key=f"edit_session_{s_id}"):
                    st.session_state[f"editing_session_{s_id}"] = True
                    safe_rerun()

                # inline edit
                if st.session_state.get(f"editing_session_{s_id}"):
                    with st.form(f"edit_session_form_{s_id}"):
                        new_name = st.text_input("Session Name", value=s["name"])
                        try:
                            cur_date = date.fromisoformat(s["date"]) if s["date"] else date.today()
                        except Exception:
                            cur_date = date.today()
                        new_date = st.date_input("Session Date", value=cur_date)
                        new_desc = st.text_area("Description", value=s["description"] or "", height=80)
                        csave, ccancel, cdelete = st.columns([1,1,1])
                        do_save = csave.form_submit_button("Save")
                        do_cancel = ccancel.form_submit_button("Cancel")
                        do_delete = cdelete.form_submit_button("Delete")
                        if do_save:
                            try:
                                with db_transaction() as conn:
                                    update_session(conn, s_id, new_name, new_date.isoformat(), new_desc)
                                    log_action(current_username, "edit_session", f"{s['name']} -> {new_name}")
                                st.success("Session updated.")
                                st.session_state[f"editing_session_{s_id}"] = False
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to save session: {e}")
                        if do_cancel:
                            st.session_state[f"editing_session_{s_id}"] = False
                            safe_rerun()
                        if do_delete:
                            try:
                                with db_transaction() as conn:
                                    delete_session(conn, s_id)
                                    log_action(current_username, "delete_session", s["name"])
                                st.success("Session deleted.")
                                if st.session_state.get("viewing_session_id") == s_id:
                                    st.session_state["viewing_session_id"] = None
                                st.session_state[f"editing_session_{s_id}"] = False
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Unable to delete session: {e}")

        # Button to view all participants
        if st.button("📋 View all participants"):
            st.session_state["viewing_session_id"] = None
            safe_rerun()

        # =========================
        # Participant management UI (separate from sessions)
        # =========================
        st.header(f"👥 Participants — {current}  {'(Viewing session)' if st.session_state.get('viewing_session_id') else ''}")

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
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                submitted = st.form_submit_button("Add Participant")
                if submitted:
                    try:
                        with db_transaction() as conn:
                            photo_path = save_photo_file(photo, current_username, current) if photo else None
                            conn.execute("""
                                INSERT INTO participants
                                (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, number, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, photo_path))
                            log_action(current_username, "add_participant", pname)
                        st.success("Participant added!")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to add participant: {e}")

        # fetch participants (either all for project or only those in viewing session)
        viewing_session_id = st.session_state.get("viewing_session_id")
        with db_connect() as conn:
            cur = conn.cursor()
            if viewing_session_id:
                # participants in that session
                cur.execute("""
                    SELECT p.* FROM participants p
                    JOIN session_participants sp ON sp.participant_id = p.id
                    WHERE sp.session_id = ?
                    ORDER BY p.id
                """, (viewing_session_id,))
                participants = cur.fetchall()
                # Also fetch session name for header & export label
                session_row = get_session_by_id(conn, viewing_session_id)
            else:
                cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                participants = cur.fetchall()
                session_row = None

        if not participants:
            st.info("No participants yet (for selected view).")
        else:
            # Bulk operations area (multi-select + target session + move/copy)
            st.markdown("**Bulk operations** — choose participants then copy or move them to a session")
            # build list of choices
            participant_choices = [f"{safe_field(p,'name','Unnamed')} (#{safe_field(p,'number','')}) — id:{safe_field(p,'id')}" for p in participants]
            id_map = {participant_choices[i]: participants[i]["id"] for i in range(len(participants))}
            chosen = st.multiselect("Select participants to move/copy", participant_choices)
            # choose target session
            with db_connect() as conn:
                all_sessions = list_sessions_for_project(conn, project_id)
            session_options = [f"{s['name']} — {s['date'] or 'no date'} (id:{s['id']})" for s in all_sessions]
            session_map = {session_options[i]: all_sessions[i]["id"] for i in range(len(all_sessions))}
            target_session_sel = st.selectbox("Target session", ["-- choose session --"] + session_options)
            action_choice = st.radio("Action", ["move (cut)", "copy"], index=0, horizontal=True)
            if st.button("Execute bulk operation"):
                if not chosen:
                    st.error("Select at least one participant")
                elif target_session_sel == "-- choose session --":
                    st.error("Choose a target session")
                else:
                    participant_ids = [id_map[c] for c in chosen]
                    target_id = session_map[target_session_sel]
                    try:
                        with db_transaction() as conn:
                            res = bulk_move_copy_participants(conn, participant_ids, target_id, action="move" if action_choice.startswith("move") else "copy")
                            log_action(current_username, "bulk_"+("move" if action_choice.startswith("move") else "copy"), f"to session {target_id} participants {participant_ids}")
                        st.success(f"Bulk operation complete. Added {res['added']}, removed {res['removed']}, skipped {res['skipped']}.")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to complete bulk operation: {e}")

            # display participants in letterbox cards + show assigned sessions (list)
            for p in participants:
                pid = p["id"]
                left, right = st.columns([9,1])
                display_path = thumb_path_for(p["photo_path"])
                data_uri = image_b64_for_path(display_path) if display_path else None
                if data_uri:
                    img_tag = f"<img class='photo' src='{data_uri}' alt='photo'/>"
                else:
                    img_tag = "<div class='photo' style='display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"

                # gather sessions for this participant (limit to this project)
                with db_connect() as conn:
                    s_rows = sessions_for_participant(conn, pid)
                if s_rows:
                    sess_names = ", ".join([f"{sr['name']}" for sr in s_rows])
                else:
                    sess_names = "Unassigned"

                name_html = (p["name"] or "Unnamed")
                number_html = (p["number"] or "")
                role_html = p["role"] or ""
                age_html = p["age"] or ""
                agency_html = p["agency"] or ""
                height_html = p["height"] or ""
                waist_html = p["waist"] or ""
                dress_html = p["dress_suit"] or ""
                avail_html = p["availability"] or ""

                card_html = f"""
                    <div class="participant-letterbox">
                        {img_tag}
                        <div class="name">{name_html} <span class="small">#{number_html}</span></div>
                        <div class="meta">Role: {role_html} • Age: {age_html}</div>
                        <div class="meta">Agency: {agency_html}</div>
                        <div class="meta">Height: {height_html} • Waist: {waist_html} • Dress/Suit: {dress_html}</div>
                        <div class="small">Availability: {avail_html}</div>
                        <div class="small" style="margin-top:6px;"><strong>Sessions:</strong> {sess_names}</div>
                    </div>
                """
                left.markdown(card_html, unsafe_allow_html=True)

                # Edit/Delete controls on right column
                if right.button("Edit", key=f"edit_{pid}"):
                    # open inline edit form
                    with st.form(f"edit_participant_{pid}"):
                        enumber = st.text_input("Number", value=p["number"] or "")
                        ename = st.text_input("Name", value=p["name"] or "")
                        erole = st.text_input("Role", value=p["role"] or "")
                        eage = st.text_input("Age", value=p["age"] or "")
                        eagency = st.text_input("Agency", value=p["agency"] or "")
                        eheight = st.text_input("Height", value=p["height"] or "")
                        ewaist = st.text_input("Waist", value=p["waist"] or "")
                        edress = st.text_input("Dress/Suit", value=p["dress_suit"] or "")
                        eavail = st.text_input("Next Availability", value=p["availability"] or "")
                        ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                        # allow quick assignment to session(s)
                        with db_connect() as conn:
                            all_sessions = list_sessions_for_project(conn, project_id)
                        session_ids_assigned = [s["id"] for s in sessions_for_participant(db_connect(), pid)]
                        # show multi-select list of session names (pre-selected)
                        sess_options = {f"{s['name']} — {s['date'] or 'no date'} (id:{s['id']})": s["id"] for s in all_sessions}
                        sess_selected = []
                        for k,v in sess_options.items():
                            if v in session_ids_assigned:
                                sess_selected.append(k)
                        sess_chosen = st.multiselect("Assign to sessions (participant will be added to selected sessions)", list(sess_options.keys()), default=sess_selected)
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
                                    conn.execute("""
                                        UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=?
                                        WHERE id=?
                                    """, (enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, pid))
                                    # update session assignments: first remove existing associations for this project, then add selected
                                    # remove participant from all sessions of this project
                                    c = conn.cursor()
                                    c.execute("""
                                        DELETE FROM session_participants WHERE participant_id=? AND session_id IN (
                                            SELECT id FROM sessions WHERE project_id=?
                                        )
                                    """, (pid, project_id))
                                    # add back selected
                                    for k in sess_chosen:
                                        sid = sess_options.get(k)
                                        if sid:
                                            add_participant_to_session(conn, sid, pid)
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
                            if isinstance(p["photo_path"], str) and os.path.exists(p["photo_path"]):
                                remove_media_file(p["photo_path"])
                            conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                            # also delete from session_participants
                            conn.execute("DELETE FROM session_participants WHERE participant_id=?", (pid,))
                            log_action(current_username, "delete_participant", p["name"] or "")
                        st.warning("Participant deleted")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to delete participant: {e}")

        # ------------------------
        # Export to Word (session-aware)
        # ------------------------
        st.subheader("📄 Export Participants (Word)")
        if st.button("Download Word File of Current View"):
            try:
                with db_connect() as conn:
                    cur = conn.cursor()
                    if st.session_state.get("viewing_session_id"):
                        # export participants in the selected session
                        sid = st.session_state["viewing_session_id"]
                        cur.execute("""
                            SELECT p.* FROM participants p
                            JOIN session_participants sp ON sp.participant_id = p.id
                            WHERE sp.session_id = ?
                            ORDER BY p.id
                        """, (sid,))
                        parts = cur.fetchall()
                        # get session name for filename
                        srow = get_session_by_id(conn, sid)
                        fname_base = f"{current}_session_{srow['name']}" if srow else f"{current}_session_{sid}"
                    else:
                        cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
                        parts = cur.fetchall()
                        fname_base = f"{current}_participants"
                    if not parts:
                        st.info("No participants to export for this view.")
                    else:
                        doc = Document()
                        heading = f"Participants - {current}"
                        if st.session_state.get("viewing_session_id"):
                            heading += f" - Session: {srow['name']}"
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

                            info_text = (
                                f"Number: {safe_field(p, 'number','')}\n"
                                f"Name: {safe_field(p, 'name','')}\n"
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
                        filename = f"{fname_base}.docx".replace(" ", "_")
                        st.download_button(
                            label="Click to download Word file",
                            data=word_stream,
                            file_name=filename,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
            except Exception as e:
                st.error(f"Unable to generate Word file: {e}")

        # Admin dashboard unchanged but visible to Admin
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

            # ------------------------
            # Database Manager (Admin-only)
            # ------------------------
            st.subheader("🗄️ Database Manager")

            st.markdown("**Browse tables | Schema | Data (paginated)**")
            # list tables
            try:
                with db_connect() as conn:
                    c = conn.cursor()
                    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
                    table_rows = c.fetchall()
                    tables = [r["name"] for r in table_rows]
            except Exception as e:
                tables = []
                st.error(f"Unable to list tables: {e}")

            if not tables:
                st.info("No tables found in the database.")
            else:
                chosen_table = st.selectbox("Select table to inspect", ["-- choose table --"] + tables)
                if chosen_table and chosen_table != "-- choose table --":
                    # show schema
                    try:
                        with db_connect() as conn:
                            cur = conn.cursor()
                            cur.execute(f"PRAGMA table_info('{chosen_table}')")
                            schema_rows = cur.fetchall()
                            schema_display = []
                            for col in schema_rows:
                                schema_display.append({
                                    "cid": col["cid"],
                                    "name": col["name"],
                                    "type": col["type"],
                                    "notnull": bool(col["notnull"]),
                                    "default": col["dflt_value"],
                                    "pk": bool(col["pk"]) }
                                )
                            st.markdown("**Schema**")
                            st.table(schema_display)
                    except Exception as e:
                        st.error(f"Unable to get schema: {e}")

                    # pagination controls for table data
                    try:
                        with db_connect() as conn:
                            cur = conn.cursor()
                            count_row = cur.execute(f"SELECT COUNT(*) as c FROM '{chosen_table}'").fetchone()
                            total_count = count_row["c"] if count_row else 0
                    except Exception as e:
                        total_count = 0
                        st.error(f"Unable to count rows: {e}")

                    per_page = st.number_input("Rows per page", min_value=1, max_value=500, value=30, step=10, key=f"perpage_{chosen_table}")
                    total_pages = max(1, (total_count + per_page - 1) // per_page)
                    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, key=f"page_{chosen_table}")
                    offset = (page - 1) * per_page

                    # fetch and show page
                    try:
                        with db_connect() as conn:
                            cur = conn.cursor()
                            cur.execute(f"SELECT * FROM '{chosen_table}' LIMIT ? OFFSET ?", (per_page, offset))
                            rows = cur.fetchall()
                            data = [dict(r) for r in rows]
                            st.markdown(f"**Showing page {page} / {total_pages} — {total_count} rows total**")
                            st.dataframe(data)
                    except Exception as e:
                        st.error(f"Unable to fetch table data: {e}")

            # ------------------------
            # Full Site Backup / Restore (DB + media)
            # ------------------------
            st.markdown("---")
            st.subheader("🗄️ Full Site Backup & Restore (DB + media)")

            db_dir = os.path.dirname(os.path.abspath(DB_FILE)) or "."
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            def make_full_backup_zip(target_zip_path):
                """Create a zip containing data.db (if exists) and the media directory (if exists)."""
                try:
                    with zipfile.ZipFile(target_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        # add DB file (at top-level inside zip as 'data.db')
                        if os.path.exists(DB_FILE):
                            zf.write(DB_FILE, arcname="data.db")
                        # add media directory (preserve relative paths)
                        if os.path.exists(MEDIA_DIR):
                            for root, dirs, files in os.walk(MEDIA_DIR):
                                for f in files:
                                    full = os.path.join(root, f)
                                    zf.write(full, arcname=os.path.join("media", os.path.relpath(full, MEDIA_DIR)))
                    return True, None
                except Exception as e:
                    return False, str(e)

            # Create backup on disk (in DB dir) and present download button
            try:
                # create a temp zip in same dir as DB so it's on same filesystem (avoid cross-device issues)
                backup_zip_name = f"site_backup_{timestamp}.zip"
                backup_zip_path = os.path.join(db_dir, backup_zip_name)

                # Make the zip (overwrite if exists)
                ok, err = make_full_backup_zip(backup_zip_path)
                if ok and os.path.exists(backup_zip_path):
                    with open(backup_zip_path, "rb") as f:
                        zip_bytes = f.read()
                    st.download_button(
                        label="📥 Download Full Site Backup (DB + media)",
                        data=zip_bytes,
                        file_name=backup_zip_name,
                        mime="application/zip"
                    )
                    st.write(f"Backup created: **{backup_zip_name}** (includes `data.db` and `media/`)")
                else:
                    st.error(f"Unable to create backup: {err}")
            except Exception as e:
                st.error(f"Backup error: {e}")

            st.markdown("---")
            st.markdown("**Restore full site from `.zip` (will overwrite DB and media).**")
            st.write("Upload a site backup `.zip` created by this tool. This will overwrite your current `data.db` and `media/` folder. The current DB and media will be copied to timestamped `.bak` locations before overwrite (best-effort).")

            uploaded_zip = st.file_uploader("Upload site backup `.zip`", type=["zip"])
            if uploaded_zip is not None:
                st.warning("⚠️ Restoring will overwrite current data (DB and media). This operation is destructive.")
                confirm_restore = st.checkbox("I confirm I want to restore the site from this .zip and understand this will overwrite current data.", key="confirm_full_restore")
                if confirm_restore:
                    if st.button("Restore Full Site Now"):
                        try:
                            # read bytes
                            zip_bytes = uploaded_zip.read()
                            # ensure write access to DB dir
                            os.makedirs(db_dir, exist_ok=True)

                            # write uploaded zip as a temp file in same db_dir
                            tmp_zip_path = None
                            try:
                                with tempfile.NamedTemporaryFile(dir=db_dir, prefix="restore_zip_", suffix=".zip", delete=False) as tf:
                                    tmp_zip_path = tf.name
                                    tf.write(zip_bytes)
                                    tf.flush()
                                    os.fsync(tf.fileno())
                            except Exception as e:
                                raise RuntimeError(f"Unable to write temporary zip in DB directory: {e}")

                            # extract into a temp extraction dir inside DB dir (so moves are on same fs)
                            extract_tmp_dir = tempfile.mkdtemp(dir=db_dir, prefix="restore_extract_")
                            try:
                                with zipfile.ZipFile(tmp_zip_path, "r") as zf:
                                    # basic validation: require at least a data.db in zip OR a media/ folder
                                    namelist = zf.namelist()
                                    if "data.db" not in namelist and not any(n.startswith("media/") for n in namelist):
                                        raise RuntimeError("Uploaded zip does not contain 'data.db' nor 'media/' — aborting restore.")
                                    zf.extractall(path=extract_tmp_dir)
                            except Exception as e:
                                # cleanup and re-raise
                                try:
                                    if os.path.exists(tmp_zip_path):
                                        os.remove(tmp_zip_path)
                                except Exception:
                                    pass
                                shutil.rmtree(extract_tmp_dir, ignore_errors=True)
                                raise

                            # best-effort backup current DB and media
                            try:
                                if os.path.exists(DB_FILE):
                                    db_backup_path = f"{DB_FILE}.bak_{timestamp}"
                                    shutil.copy2(DB_FILE, db_backup_path)
                            except Exception:
                                # ignore but continue
                                pass
                            try:
                                if os.path.exists(MEDIA_DIR):
                                    media_backup_path = f"{MEDIA_DIR}.bak_{timestamp}"
                                    try:
                                        os.rename(MEDIA_DIR, media_backup_path)
                                    except Exception:
                                        shutil.copytree(MEDIA_DIR, media_backup_path)
                            except Exception:
                                pass

                            # clear streamlit cached DB resource BEFORE replacing so connections are released
                            try:
                                st.cache_resource.clear()
                            except Exception:
                                pass

                            # Replace DB if present in extracted dir
                            extracted_db_path = os.path.join(extract_tmp_dir, "data.db")
                            if os.path.exists(extracted_db_path):
                                # create temp path in same dir as DB and move
                                tmp_db_path = None
                                try:
                                    with tempfile.NamedTemporaryFile(dir=db_dir, prefix="restore_db_", suffix=".db", delete=False) as tf:
                                        tmp_db_path = tf.name
                                        # copy extracted DB bytes into tmp_db_path
                                        with open(extracted_db_path, "rb") as ef:
                                            shutil.copyfileobj(ef, tf)
                                        tf.flush()
                                        os.fsync(tf.fileno())
                                    # atomically replace
                                    os.replace(tmp_db_path, DB_FILE)
                                except Exception as e:
                                    # cleanup and abort
                                    try:
                                        if tmp_db_path and os.path.exists(tmp_db_path):
                                            os.remove(tmp_db_path)
                                    except Exception:
                                        pass
                                    raise RuntimeError(f"Failed to replace DB file: {e}")

                            # Replace media folder if present in extracted dir
                            extracted_media_dir = os.path.join(extract_tmp_dir, "media")
                            if os.path.exists(extracted_media_dir):
                                # remove existing media folder (if present) and move extracted one into place
                                try:
                                    if os.path.exists(MEDIA_DIR):
                                        shutil.rmtree(MEDIA_DIR)
                                except Exception:
                                    pass
                                # try atomic move
                                try:
                                    shutil.move(extracted_media_dir, MEDIA_DIR)
                                except Exception:
                                    # fallback to copytree
                                    try:
                                        shutil.copytree(extracted_media_dir, MEDIA_DIR)
                                        shutil.rmtree(extracted_media_dir, ignore_errors=True)
                                    except Exception as e:
                                        raise RuntimeError(f"Failed to install media folder: {e}")

                            # cleanup tmp files
                            try:
                                if os.path.exists(tmp_zip_path):
                                    os.remove(tmp_zip_path)
                            except Exception:
                                pass
                            # remove extraction temp dir (if media was moved, it may already be empty)
                            try:
                                shutil.rmtree(extract_tmp_dir, ignore_errors=True)
                            except Exception:
                                pass

                            log_action(current_username, "full_restore", f"restored_by={current_username}")
                            st.success("Full site restored successfully (DB + media). App will reload to use restored data.")
                            safe_rerun()

                        except Exception as e:
                            st.error(f"Full restore failed: {e}")

            st.markdown("---")
            st.write("Note: Schema edits are not supported from this UI. Use a separate DB tool for advanced schema changes.")
          
          # ---------------- Safe preview + restore block ----------------
uploaded_zip = st.file_uploader("Upload site backup `.zip` (preview before restore)", type=["zip"])
if uploaded_zip is not None:
    st.warning("This tool will preview the uploaded backup. No data will be overwritten until you confirm the final restore.")
    try:
        zip_bytes = uploaded_zip.read()
        db_dir = os.path.dirname(os.path.abspath(DB_FILE)) or "."
        os.makedirs(db_dir, exist_ok=True)

        # write uploaded zip to DB dir (avoid cross-device rename issues)
        tmp_zip_path = None
        with tempfile.NamedTemporaryFile(dir=db_dir, prefix="preview_zip_", suffix=".zip", delete=False) as tf:
            tmp_zip_path = tf.name
            tf.write(zip_bytes)
            tf.flush()
            os.fsync(tf.fileno())

        # extract to a temp dir inside db_dir
        extract_tmp_dir = tempfile.mkdtemp(dir=db_dir, prefix="preview_extract_")
        with zipfile.ZipFile(tmp_zip_path, "r") as zf:
            namelist = zf.namelist()
            st.write("Contents preview (top-level):", [n for n in namelist if "/" not in n or n.startswith("media/")][:200])
            zf.extractall(path=extract_tmp_dir)

        # Look for data.db in extracted content
        extracted_db_path = os.path.join(extract_tmp_dir, "data.db")
        found_db = os.path.exists(extracted_db_path)

        users_count = projects_count = participants_count = None
        sample_users = []
        sample_projects = []

        if found_db:
            try:
                # open the extracted DB read-only
                # We open normally then close immediately after reading counts
                conn_preview = sqlite3.connect(extracted_db_path)
                conn_preview.row_factory = sqlite3.Row
                cur = conn_preview.cursor()
                # get counts (guard with try/except if tables missing)
                try:
                    users_count = cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
                except Exception:
                    users_count = None
                try:
                    projects_count = cur.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
                except Exception:
                    projects_count = None
                try:
                    participants_count = cur.execute("SELECT COUNT(*) AS c FROM participants").fetchone()["c"]
                except Exception:
                    participants_count = None

                # sample rows
                try:
                    sample_users = [dict(r) for r in cur.execute("SELECT id, username, role, last_login FROM users ORDER BY id LIMIT 20").fetchall()]
                except Exception:
                    sample_users = []
                try:
                    sample_projects = [dict(r) for r in cur.execute("SELECT id, user_id, name, created_at FROM projects ORDER BY id LIMIT 20").fetchall()]
                except Exception:
                    sample_projects = []

                conn_preview.close()
            except Exception as e:
                st.error(f"Unable to read extracted DB for preview: {e}")
                found_db = False

        # Show preview summary
        st.markdown("### Preview Summary")
        if found_db:
            st.markdown(f"- Found `data.db` in uploaded zip.")
            st.markdown(f"- Users: **{users_count if users_count is not None else 'unknown (table may be missing)'}**")
            st.markdown(f"- Projects: **{projects_count if projects_count is not None else 'unknown'}**")
            st.markdown(f"- Participants: **{participants_count if participants_count is not None else 'unknown'}**")
            if sample_users:
                st.markdown("#### Sample users (first 20)")
                st.table(sample_users)
            if sample_projects:
                st.markdown("#### Sample projects (first 20)")
                st.table(sample_projects)
        else:
            st.warning("No `data.db` found inside the uploaded zip. The zip may only contain `media/` or be structured differently.")

        # Offer to proceed with destructive restore (only if previewed)
        proceed = st.checkbox("I have reviewed the preview above and want to proceed with the full destructive restore (overwrite DB + media).", key="confirm_restore_preview")
        if proceed:
            if st.button("Perform Full Restore Now"):
                try:
                    # backup existing DB and media (best-effort)
                    timestamp_now = datetime.now().strftime("%Y%m%d_%H%M%S")
                    try:
                        if os.path.exists(DB_FILE):
                            shutil.copy2(DB_FILE, f"{DB_FILE}.bak_{timestamp_now}")
                    except Exception:
                        pass
                    try:
                        if os.path.exists(MEDIA_DIR):
                            # rename if possible, else copy
                            try:
                                os.rename(MEDIA_DIR, f"{MEDIA_DIR}.bak_{timestamp_now}")
                            except Exception:
                                shutil.copytree(MEDIA_DIR, f"{MEDIA_DIR}.bak_{timestamp_now}")
                    except Exception:
                        pass

                    # clear cache before replacing files
                    try:
                        st.cache_resource.clear()
                    except Exception:
                        pass

                    # Replace DB if present in extracted dir
                    if found_db:
                        # create temp DB file in same dir as DB_FILE and copy extracted DB into it
                        tmp_db_path = None
                        with tempfile.NamedTemporaryFile(dir=db_dir, prefix="restore_db_", suffix=".db", delete=False) as tf_db:
                            tmp_db_path = tf_db.name
                            with open(extracted_db_path, "rb") as ef:
                                shutil.copyfileobj(ef, tf_db)
                            tf_db.flush()
                            os.fsync(tf_db.fileno())
                        # atomic replace (same filesystem because tmp_db_path is in db_dir)
                        os.replace(tmp_db_path, DB_FILE)

                    # Replace media if present in extracted dir
                    extracted_media_dir = os.path.join(extract_tmp_dir, "media")
                    if os.path.exists(extracted_media_dir):
                        # remove current media, then move extracted in place
                        try:
                            if os.path.exists(MEDIA_DIR):
                                shutil.rmtree(MEDIA_DIR)
                        except Exception:
                            pass
                        try:
                            shutil.move(extracted_media_dir, MEDIA_DIR)
                        except Exception:
                            # fallback: copytree
                            shutil.copytree(extracted_media_dir, MEDIA_DIR)
                            shutil.rmtree(extracted_media_dir, ignore_errors=True)

                    # cleanup temp zip and extraction dir
                    try:
                        if os.path.exists(tmp_zip_path):
                            os.remove(tmp_zip_path)
                    except Exception:
                        pass
                    try:
                        shutil.rmtree(extract_tmp_dir, ignore_errors=True)
                    except Exception:
                        pass

                    log_action(current_username, "full_restore", f"restored_by={current_username}")
                    st.success("Full restore completed. App will reload now.")
                    safe_rerun()

                except Exception as e:
                    st.error(f"Restore failed during execution: {e}")
    except Exception as e:
        st.error(f"Error processing uploaded zip: {e}")

# ========================
# End of file
# ========================

# Notes:
# - This file includes the Admin-only Full Site Backup & Restore tools under the Admin Dashboard.
# - To run: `streamlit run sachas_casting_manager_with_full_backup.py` from the directory containing this file.
# - Ensure the process has write permissions to the app directory so backups and restores can write files.
# - If you want me to also save this file as a downloadable attachment here, tell me and I'll attach it.

