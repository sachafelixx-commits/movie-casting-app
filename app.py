# sachas_casting_manager_admin_fixed.py
# Sacha's Casting Manager — Admin UI fixed so admin tools only render after login
# Complete app file with admin dashboard moved behind login+role guard.

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
import traceback

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
# Minimal CSS
# ========================
st.markdown("""
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
.participant-letterbox { max-width: 520px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.06); padding: 8px; margin-bottom: 12px; background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,0.04); }
.participant-letterbox .photo { width: 100%; height: 220px; display:block; object-fit: cover; border-radius: 8px; background: #f6f6f6; margin-bottom: 8px; }
.participant-letterbox .name { font-weight: 700; font-size: 1.05rem; margin-bottom: 6px; color: #000 !important; }
.participant-letterbox .meta { color: rgba(0,0,0,0.6); font-size: 0.95rem; margin-bottom: 4px; }
.participant-letterbox .small { color: rgba(0,0,0,0.55); font-size: 0.9rem; }
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
# Image helpers
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
# SQLite helpers + migration
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
    c.execute("DELETE FROM session_participants WHERE session_id=?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

def add_participant_to_session(conn, session_id, participant_id):
    c = conn.cursor()
    now = datetime.now().isoformat()
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
    c = conn.cursor()
    target_session = get_session_by_id(conn, target_session_id)
    if not target_session:
        raise ValueError("Target session not found")
    proj_id = target_session["project_id"]
    now = datetime.now().isoformat()
    results = {"added":0,"skipped":0,"removed":0}
    for pid in participant_ids:
        if action == "move":
            c.execute("""
                SELECT sp.id, sp.session_id FROM session_participants sp
                JOIN sessions s ON s.id = sp.session_id
                WHERE sp.participant_id=? AND s.project_id=?
            """, (pid, proj_id))
            rows = c.fetchall()
            for r in rows:
                if r["session_id"] != target_session_id:
                    c.execute("DELETE FROM session_participants WHERE id=?", (r["id"],))
                    results["removed"] += 1
            c.execute("SELECT id FROM session_participants WHERE session_id=? AND participant_id=?", (target_session_id, pid))
            if not c.fetchone():
                c.execute("INSERT INTO session_participants (session_id, participant_id, added_at) VALUES (?, ?, ?)", (target_session_id, pid, now))
                results["added"] += 1
            else:
                results["skipped"] += 1
        else:
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
    st.session_state["viewing_session_id"] = None
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
# After login: main app (Admin UI only renders via function below)
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
# SESSIONS manager (restore full sessions UI)
# =========================
st.header("🗂 Sessions")

# ensure we have the active project object
with db_connect() as conn:
    proj = get_project_by_name(conn, user_id, st.session_state.get("current_project_name") or DEFAULT_PROJECT_NAME)
    active_project_id = proj["id"] if proj else None

# Create session form
with st.expander("➕ Create New Session", expanded=False):
    with st.form("new_session_form"):
        s_name = st.text_input("Session name")
        s_date = st.date_input("Session date", value=None)
        s_desc = st.text_area("Description", height=80)
        create_sess = st.form_submit_button("Create Session")
        if create_sess:
            if not s_name:
                st.error("Provide a session name")
            elif not active_project_id:
                st.error("Active project not found")
            else:
                try:
                    with db_transaction() as conn:
                        create_session(conn, active_project_id, s_name, s_date.isoformat() if s_date else None, s_desc or "")
                        log_action(current_username, "create_session", f"{s_name} @ project_id={active_project_id}")
                    st.success(f"Session '{s_name}' created.")
                    safe_rerun()
                except Exception as e:
                    st.error(f"Unable to create session: {e}")

# Load sessions for project
conn_read = get_db_conn()
sessions = list_sessions_for_project(conn_read, active_project_id) if active_project_id else []
session_rows = [(s["id"], s["name"], s.get("date"), s.get("description"), s.get("created_at")) for s in sessions]

# UI: Sessions list and actions
if not session_rows:
    st.info("No sessions yet for this project.")
else:
    st.subheader("Sessions for active project")
    for sid, sname, sdate, sdesc, screated in session_rows:
        col1, col2, col3 = st.columns([4,2,4])
        col1.markdown(f"**{sname}**")
        col2.markdown(sdate.split('T')[0] if sdate else "—")
        col3.markdown(sdesc or "—")
        btn_view, btn_edit, btn_delete = st.columns([1,1,1])
        if btn_view.button("View", key=f"view_sess_{sid}"):
            st.session_state["viewing_session_id"] = sid
            safe_rerun()
        if btn_edit.button("Edit", key=f"edit_sess_{sid}"):
            # inline edit modal via form
            st.session_state["editing_session_id"] = sid
            safe_rerun()
        if btn_delete.button("Delete", key=f"del_sess_{sid}"):
            st.session_state["confirm_delete_session"] = sid
            safe_rerun()

        # Inline edit form
        if st.session_state.get("editing_session_id") == sid:
            with st.form(f"edit_session_form_{sid}"):
                new_name = st.text_input("Session name", value=sname)
                try:
                    new_date = st.date_input("Session date", value=(sdate.split("T")[0] if sdate else None))
                except Exception:
                    new_date = None
                new_desc = st.text_area("Description", value=sdesc or "", height=120)
                save_s = st.form_submit_button("Save")
                cancel_s = st.form_submit_button("Cancel")
                if save_s:
                    try:
                        with db_transaction() as conn:
                            update_session(conn, sid, new_name, new_date.isoformat() if new_date else None, new_desc)
                            log_action(current_username, "edit_session", f"{sid}")
                        st.success("Session updated.")
                        st.session_state["editing_session_id"] = None
                        safe_rerun()
                    except Exception as e:
                        st.error(f"Unable to update session: {e}")
                if cancel_s:
                    st.session_state["editing_session_id"] = None
                    safe_rerun()

        # Delete confirmation
        if st.session_state.get("confirm_delete_session") == sid:
            st.warning(f"Type session name **{sname}** to confirm deletion. This cannot be undone.")
            with st.form(f"confirm_delete_session_{sid}"):
                confirm_text = st.text_input("Confirm session name")
                d_yes = st.form_submit_button("Delete Permanently")
                d_no = st.form_submit_button("Cancel")
                if d_yes:
                    if confirm_text == sname:
                        try:
                            with db_transaction() as conn:
                                delete_session(conn, sid)
                                log_action(current_username, "delete_session", f"{sid}")
                            st.success(f"Session '{sname}' deleted.")
                            st.session_state["confirm_delete_session"] = None
                            if st.session_state.get("viewing_session_id") == sid:
                                st.session_state["viewing_session_id"] = None
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete session: {e}")
                    else:
                        st.error("Session name mismatch. Not deleted.")
                if d_no:
                    st.session_state["confirm_delete_session"] = None
                    safe_rerun()

# Session detail view: participants + add/remove + bulk operations
view_sid = st.session_state.get("viewing_session_id")
if view_sid:
    st.markdown("---")
    st.subheader("Session details")
    with db_connect() as conn:
        sess = get_session_by_id(conn, view_sid)
    if not sess:
        st.error("Session not found.")
        st.session_state["viewing_session_id"] = None
    else:
        st.markdown(f"**{sess['name']}** — {sess.get('date') or 'no date'}")
        st.markdown(sess.get("description") or "*No description*")

        # participants in project (so admin/user can add into session)
        with db_connect() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM participants WHERE project_id=? ORDER BY name COLLATE NOCASE", (active_project_id,))
            project_participants = [dict(r) for r in c.fetchall()]

        # participants currently in this session
        session_participants = participants_in_session(get_db_conn(), view_sid)
        session_part_ids = [p["id"] for p in session_participants]

        st.markdown("**Participants in this session**")
        if not session_participants:
            st.info("No participants in this session yet.")
        else:
            for p in session_participants:
                pcols = st.columns([1,4,2,2])
                thumb = thumb_path_for(p.get("photo_path")) or None
                if thumb:
                    try:
                        pcols[0].image(thumb, width=80)
                    except Exception:
                        pcols[0].write("No image")
                else:
                    pcols[0].write("—")
                pcols[1].markdown(f\"**{p.get('name') or '—'}**\\n{p.get('role') or ''}\\n{p.get('agency') or ''}\")\n                remove_key = f\"remove_part_{view_sid}_{p['id']}\"\n                if pcols[2].button(\"Remove\", key=remove_key):\n                    try:\n                        with db_transaction() as conn:\n                            remove_participant_from_session(conn, view_sid, p[\"id\"])\n                            log_action(current_username, \"remove_participant_from_session\", f\"sess={view_sid} pid={p['id']}\")\n                        st.success(\"Participant removed from session\")\n                        safe_rerun()\n                    except Exception as e:\n                        st.error(f\"Unable to remove participant: {e}\")\n                # quick view / download photo\n                if pcols[3].button(\"Download Photo\", key=f\"dlphoto_{p['id']}\"):\n                    pb = get_photo_bytes(p.get(\"photo_path\"))\n                    if pb:\n                        try:\n                            st.download_button(label=f\"Download {p.get('name')}\", data=pb, file_name=f\"{_sanitize_for_path(p.get('name') or str(p['id']))}.jpg\")\n                        except Exception:\n                            st.info(\"Unable to create download; file may be too large or missing.\")\n                    else:\n                        st.info(\"No photo available for this participant\")\n\n        st.markdown(\"---\")\n        st.subheader(\"Add participants to this session\")\n        # allow multi-select of participants not already in the session\n        available = [p for p in project_participants if p['id'] not in session_part_ids]\n        options = { f\"{p['name']} ({p.get('number') or ''}) — id:{p['id']}\": p['id'] for p in available }\n        if options:\n            chosen = st.multiselect(\"Select participants to add\", list(options.keys()))\n            add_btn = st.button(\"Add selected to session\")\n            if add_btn and chosen:\n                try:\n                    with db_transaction() as conn:\n                        for label in chosen:\n                            pid = options[label]\n                            add_participant_to_session(conn, view_sid, pid)\n                            log_action(current_username, \"add_participant_to_session\", f\"sess={view_sid} pid={pid}\")\n                    st.success(\"Selected participants added to session\")\n                    safe_rerun()\n                except Exception as e:\n                    st.error(f\"Unable to add participants: {e}\")\n        else:\n            st.info(\"No available participants to add (all are already in the session or none exist)\")\n\n        st.markdown(\"---\")\n        st.subheader(\"Bulk operations\")\n        all_session_ids = [r[0] for r in session_rows]\n        if all_session_ids:\n            target = st.selectbox(\"Target session (for move/copy)\", [s for s in all_session_ids if s != view_sid] or [None])\n            action = st.selectbox(\"Action\", [\"move\", \"copy\"])\n            sel_pids = st.multiselect(\"Choose participant IDs to move/copy\", [p['id'] for p in project_participants], format_func=lambda x: str(x))\n            if st.button(\"Perform bulk operation\"):\n                if not target:\n                    st.error(\"No target session chosen\")\n                elif not sel_pids:\n                    st.error(\"No participants selected\")\n                else:\n                    try:\n                        with db_transaction() as conn:\n                            res = bulk_move_copy_participants(conn, sel_pids, int(target), action=action)\n                            log_action(current_username, f\"bulk_{action}_participants\", f\"from={view_sid} to={target} pids={sel_pids}\")\n                        st.success(f\"Bulk {action} completed: {res}\")\n                        safe_rerun()\n                    except Exception as e:\n                        st.error(f\"Bulk op failed: {e}\")\n        else:\n            st.info(\"No other sessions to target for bulk move/copy\")\n```

Notes / behavior
- This UI:
  - Lets you create/edit/delete sessions for the active project.
  - Lets you view a session, see participants, remove participants from the session.
  - Lets you add participants from the project's participant pool to the session (multi-select).
  - Supports bulk move/copy of participants between sessions via the existing helper `bulk_move_copy_participants`.
  - Uses `db_transaction()` everywhere for safe commits and `log_action()` for audit.
- It assumes `st.session_state["current_project_name"]` is set (your project manager already maintains that).
- If you'd like:
  - I can also add CSV export of session participant lists.
  - Or add a "reassign owner" function to reassign projects to another username (useful occasionally).
  - Or automatically create a default session when a project is created.

If you'd prefer, I can insert this block into the canvas file I created earlier and attach the updated `.py` for download. Which would you like?

        # ------------------------
        # Admin Dashboard: only render if role is Admin
        # ------------------------
        def render_admin_dashboard(current_username):
            st.header("👑 Admin Dashboard")
            # Refresh button
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
            # Reliable backup & in-memory download (uses sqlite backup API)
            # ------------------------
            st.markdown("---")
            st.subheader("🔒 Reliable Backup (in-memory, includes WAL)")

            def counts_from_conn(conn):
                cur = conn.cursor()
                def safe(q):
                    try:
                        return cur.execute(q).fetchone()[0]
                    except Exception:
                        return None
                users = safe("SELECT COUNT(*) FROM users")
                projects = safe("SELECT COUNT(*) FROM projects")
                participants = safe("SELECT COUNT(*) FROM participants")
                sample = []
                try:
                    cur.execute("SELECT id, username, role, last_login FROM users ORDER BY id LIMIT 10")
                    sample = [dict(r) for r in cur.fetchall()]
                except Exception:
                    sample = []
                return users, projects, participants, sample

            def build_reliable_backup_bytes():
                db_dir = os.path.dirname(os.path.abspath(DB_FILE)) or "."
                tmp_db_fd, tmp_db_path = tempfile.mkstemp(prefix="backup_copy_", suffix=".db", dir=db_dir)
                os.close(tmp_db_fd)
                try:
                    src_conn = get_db_conn()
                    dest_conn = sqlite3.connect(tmp_db_path)
                    try:
                        src_conn.backup(dest_conn, pages=0)
                        dest_conn.commit()
                    finally:
                        dest_conn.close()
                    verify_conn = sqlite3.connect(tmp_db_path)
                    verify_conn.row_factory = sqlite3.Row
                    users_cnt, projects_cnt, participants_cnt, sample_users = counts_from_conn(verify_conn)
                    verify_conn.close()
                    bio = io.BytesIO()
                    manifest = {"created_at": datetime.now().isoformat(), "db_path": os.path.abspath(DB_FILE), "users_count": users_cnt, "projects_count": projects_cnt, "participants_count": participants_cnt, "sample_users": sample_users}
                    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        zf.write(tmp_db_path, arcname="data.db")
                        if os.path.exists(MEDIA_DIR):
                            for root, dirs, files in os.walk(MEDIA_DIR):
                                for fname in files:
                                    full = os.path.join(root, fname)
                                    rel = os.path.relpath(full, MEDIA_DIR)
                                    zf.write(full, arcname=os.path.join("media", rel))
                        zf.writestr("manifest.json", json.dumps(manifest, default=str, indent=2))
                    bio.seek(0)
                    return bio, manifest
                finally:
                    try:
                        if os.path.exists(tmp_db_path):
                            os.remove(tmp_db_path)
                    except Exception:
                        pass

            if st.button("Create reliable in-memory backup (downloadable)"):
                try:
                    bio, manifest = build_reliable_backup_bytes()
                    st.success("Built backup successfully (this method includes WAL contents). Manifest:")
                    st.json(manifest)
                    st.download_button("📥 Download reliable backup (zip)", data=bio, file_name=f"reliable_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip", mime="application/zip")
                except Exception as e:
                    st.error(f"Failed to build reliable backup: {e}\n{traceback.format_exc()}")

            st.markdown("---")
            st.subheader("⬆️ Upload & Restore (robust, verified)")
            st.write("Upload a backup zip created by the tool above. The uploader will preview the uploaded DB and show project ownership details. Only after you confirm will it replace the active dataset.")

            # The robust restore implementation (same as discussed earlier)
            uploaded_zip = st.file_uploader("Upload backup .zip to restore (this will replace the active dataset)", type=["zip"]) 
            if uploaded_zip is not None:
                st.warning("Preview will run. Nothing will be overwritten until you confirm. This tool WILL CLOSE DB connections first to ensure the replacement is used by the app.")
                try:
                    db_dir = os.path.dirname(os.path.abspath(DB_FILE)) or "."
                    os.makedirs(db_dir, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=db_dir, prefix="upload_tmp_", suffix=".zip", delete=False) as tf:
                        tmp_zip_path = tf.name
                        uploaded_zip.seek(0)
                        tf.write(uploaded_zip.read())
                        tf.flush(); os.fsync(tf.fileno())
                    extract_dir = tempfile.mkdtemp(dir=db_dir, prefix="restore_extract_")
                    with zipfile.ZipFile(tmp_zip_path, "r") as zf:
                        namelist = zf.namelist()
                        st.write("Zip contents (sample):", namelist[:200])
                        zf.extractall(path=extract_dir)
                    candidates = []
                    for root, _, files in os.walk(extract_dir):
                        for f in files:
                            if f.lower().endswith(".db"):
                                candidates.append(os.path.join(root, f))
                    if not candidates:
                        st.error("No .db file found inside uploaded zip. Abort.")
                        try: os.remove(tmp_zip_path)
                        except Exception: pass
                        try: shutil.rmtree(extract_dir, ignore_errors=True)
                        except Exception: pass
                    else:
                        candidate_db = candidates[0]
                        st.markdown("### Preview of uploaded DB (first .db found)")
                        def preview_db_file(db_path):
                            out = {"path": db_path}
                            try:
                                conn = sqlite3.connect(db_path)
                                conn.row_factory = sqlite3.Row
                                cur = conn.cursor()
                                def safe(q):
                                    try:
                                        return cur.execute(q).fetchone()[0]
                                    except Exception:
                                        return None
                                out["users"] = safe("SELECT COUNT(*) FROM users")
                                out["projects"] = safe("SELECT COUNT(*) FROM projects")
                                out["participants"] = safe("SELECT COUNT(*) FROM participants")
                                try:
                                    out["sample_users"] = [dict(r) for r in cur.execute("SELECT id,username,role,last_login FROM users ORDER BY id LIMIT 10").fetchall()]
                                except Exception:
                                    out["sample_users"] = []
                                try:
                                    proj_rows = cur.execute("""
                                        SELECT p.id AS project_id, p.name AS project_name, p.user_id AS owner_user_id, u.username AS owner_username, p.created_at
                                        FROM projects p
                                        LEFT JOIN users u ON u.id = p.user_id
                                        ORDER BY p.id
                                        LIMIT 200
                                    """).fetchall()
                                    out["projects_detail"] = [dict(r) for r in proj_rows]
                                except Exception:
                                    out["projects_detail"] = []
                                conn.close()
                            except Exception as e:
                                out["error"] = str(e)
                            return out
                        p = preview_db_file(candidate_db)
                        if p.get("error"):
                            st.error(f"Unable to read extracted DB: {p['error']}")
                        else:
                            st.write(f"- Users: **{p.get('users')}**, Projects: **{p.get('projects')}**, Participants: **{p.get('participants')}**")
                            if p.get("sample_users"):
                                st.write("Sample users:")
                                st.table(p["sample_users"])
                            if p.get("projects_detail"):
                                st.write("Projects (first 200): project_id | project_name | owner_user_id | owner_username")
                                compact = [{ "project_id": r["project_id"], "project_name": r["project_name"], "owner_user_id": r["owner_user_id"], "owner_username": r["owner_username"] } for r in p["projects_detail"]]
                                st.table(compact)
                        st.warning("Restoring will REPLACE the active `data.db` and the `media/` folder (if present in the zip). Type 'REPLACE' to confirm.")
                        confirm_text = st.text_input("Type 'REPLACE' to enable the final restore button", key="admin_restore_confirm2")
                        if confirm_text == "REPLACE":
                            if st.button("Perform destructive restore now"):
                                try:
                                    # close cached connections & clear
                                    try:
                                        conn_cached = get_db_conn()
                                        try: conn_cached.close()
                                        except Exception: pass
                                    except Exception:
                                        pass
                                    try:
                                        st.cache_resource.clear()
                                    except Exception:
                                        pass
                                    time.sleep(0.2)
                                    # use sqlite backup API to install
                                    def safe_backup_copy_from_file(src_db_path, dst_db_path):
                                        dst_dir = os.path.dirname(os.path.abspath(dst_db_path)) or "."
                                        fd, tmp_dest = tempfile.mkstemp(prefix="restore_tmpdb_", suffix=".db", dir=dst_dir)
                                        os.close(fd)
                                        try:
                                            src_conn = sqlite3.connect(src_db_path)
                                            dest_conn = sqlite3.connect(tmp_dest)
                                            try:
                                                src_conn.backup(dest_conn, pages=0)
                                                dest_conn.commit()
                                            finally:
                                                try: dest_conn.close()
                                                except Exception: pass
                                                try: src_conn.close()
                                                except Exception: pass
                                            try:
                                                os.replace(tmp_dest, dst_db_path)
                                                try:
                                                    wal = dst_db_path + "-wal"
                                                    shm = dst_db_path + "-shm"
                                                    if os.path.exists(wal): os.remove(wal)
                                                    if os.path.exists(shm): os.remove(shm)
                                                except Exception: pass
                                                return True, None
                                            except Exception as e_replace:
                                                try:
                                                    shutil.copyfile(tmp_dest, dst_db_path)
                                                    with open(dst_db_path, "rb+") as df:
                                                        df.flush(); os.fsync(df.fileno())
                                                    try: os.remove(tmp_dest)
                                                    except Exception: pass
                                                    return True, None
                                                except Exception as e_copy:
                                                    return False, f"replace_err:{e_replace} copy_err:{e_copy}"
                                        except Exception as e:
                                            try:
                                                if os.path.exists(tmp_dest): os.remove(tmp_dest)
                                            except Exception: pass
                                            return False, str(e)
                                    ok, err = safe_backup_copy_from_file(candidate_db, DB_FILE)
                                    if not ok:
                                        raise RuntimeError(f"Failed to copy DB into place: {err}")
                                    extracted_media_dir = os.path.join(extract_dir, "media")
                                    if os.path.exists(extracted_media_dir):
                                        try:
                                            if os.path.exists(MEDIA_DIR): shutil.rmtree(MEDIA_DIR)
                                        except Exception: pass
                                        try:
                                            shutil.move(extracted_media_dir, MEDIA_DIR)
                                        except Exception:
                                            shutil.copytree(extracted_media_dir, MEDIA_DIR)
                                    else:
                                        try:
                                            if os.path.exists(MEDIA_DIR): shutil.rmtree(MEDIA_DIR)
                                        except Exception: pass
                                    try: os.remove(tmp_zip_path)
                                    except Exception: pass
                                    try: shutil.rmtree(extract_dir, ignore_errors=True)
                                    except Exception: pass
                                    try:
                                        st.cache_resource.clear()
                                    except Exception: pass
                                    time.sleep(0.2)
                                    # verification
                                    def preview_db_file_disk(db_path):
                                        out = {}
                                        try:
                                            conn = sqlite3.connect(db_path)
                                            conn.row_factory = sqlite3.Row
                                            cur = conn.cursor()
                                            def safe(q):
                                                try: return cur.execute(q).fetchone()[0]
                                                except Exception: return None
                                            out["users"] = safe("SELECT COUNT(*) FROM users")
                                            out["projects"] = safe("SELECT COUNT(*) FROM projects")
                                            out["participants"] = safe("SELECT COUNT(*) FROM participants")
                                            try:
                                                out["sample_users"] = [dict(r) for r in cur.execute("SELECT id,username,role,last_login FROM users ORDER BY id LIMIT 10").fetchall()]
                                            except Exception:
                                                out["sample_users"] = []
                                            try:
                                                rows = cur.execute("""
                                                    SELECT p.id AS project_id, p.name AS project_name, p.user_id AS owner_user_id, u.username AS owner_username, p.created_at
                                                    FROM projects p
                                                    LEFT JOIN users u ON u.id = p.user_id
                                                    ORDER BY p.id
                                                    LIMIT 200
                                                """).fetchall()
                                                out["projects_detail"] = [dict(r) for r in rows]
                                            except Exception:
                                                out["projects_detail"] = []
                                            conn.close()
                                        except Exception as e:
                                            out["error"] = str(e)
                                        return out
                                    verification = preview_db_file_disk(DB_FILE)
                                    if verification.get("error"):
                                        st.error(f"Restore completed but verification failed: {verification['error']}")
                                    else:
                                        st.success("Restore completed — verification results (on-disk DB):")
                                        st.write(f"- Users: **{verification.get('users')}**, Projects: **{verification.get('projects')}**, Participants: **{verification.get('participants')}**")
                                        if verification.get("sample_users"):
                                            st.write("Sample users (live):")
                                            st.table(verification["sample_users"])
                                        if verification.get("projects_detail"):
                                            compact = [{ "project_id": r["project_id"], "project_name": r["project_name"], "owner_user_id": r["owner_user_id"], "owner_username": r["owner_username"] } for r in verification["projects_detail"]]
                                            st.write("Projects (first 200):")
                                            st.table(compact)
                                    safe_rerun()
                                except Exception as e:
                                    st.error(f"Restore failed: {e}\n{traceback.format_exc()}")
                                    try: os.remove(tmp_zip_path)
                                    except Exception: pass
                                    try: shutil.rmtree(extract_dir, ignore_errors=True)
                                    except Exception: pass
                except Exception as e:
                    st.error(f"Error processing uploaded zip: {e}\n{traceback.format_exc()}")

        # Only render admin dashboard when role is Admin
        if role == "Admin":
            render_admin_dashboard(current_username)

# ========================
# End of file
# ========================

# Notes:
# - Admin UI now lives inside render_admin_dashboard() and is called only when the logged-in user has role 'Admin'.
# - Paste this file into your app directory and run: streamlit run sachas_casting_manager_admin_fixed.py
# - If you want me to attach this .py as a downloadable file here, tell me and I'll add it.
