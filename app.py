import streamlit as st
import sqlite3
import json
import os
import io
import base64
import uuid
import shutil
import re
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import hashlib
from contextlib import contextmanager

# ========================
# Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager (Responsive)", layout="wide")

DB_FILE = "data.db"
USERS_JSON = "users.json"
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
DEFAULT_PROJECT_NAME = "Default Project"
PRAGMA_SYNCHRONOUS = "NORMAL"

# ========================
# Inject safe responsive CSS (single injection)
# ========================
st.markdown(
    """
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
    /* Layout container */
    .block-container{max-width:1200px; padding-left:1rem; padding-right:1rem;}

    /* Buttons touch-friendly */
    .stButton>button, button{padding:.6rem 1rem !important; font-size:1rem !important;}

    /* Participant card */
    .participant-card{display:flex;flex-wrap:wrap;align-items:flex-start;gap:12px;margin-bottom:12px;padding:12px;border-radius:10px;border:1px solid rgba(0,0,0,0.06);background:#fff;box-shadow:0 1px 4px rgba(0,0,0,0.04)}
    .participant-photo{flex:0 0 96px}
    .participant-photo img{width:96px;height:96px;object-fit:cover;border-radius:8px}
    .participant-info{flex:1 1 220px;min-width:160px}
    .participant-meta{color:rgba(0,0,0,0.55);font-size:0.95rem;margin-top:6px}

    /* Project row helpers */
    .project-row{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.03)}
    .project-name{flex:3}
    .project-desc{flex:4}
    .project-created{flex:2}
    .project-count{flex:1}
    .project-actions{flex:3;display:flex;gap:8px}

    /* Responsive tweaks */
    @media (max-width:900px){
      .block-container{padding-left:0.6rem;padding-right:0.6rem}
      .participant-photo{flex-basis:72px}
      .participant-photo img{width:72px;height:72px}
    }
    @media (max-width:600px){
      .participant-card{flex-direction:row}
      .participant-info{min-width:140px}
      h1{font-size:1.3rem}
    }

    img{max-width:100% !important;height:auto !important}
    </style>
    """,
    unsafe_allow_html=True,
)

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


def save_photo_file(uploaded_file, username: str, project_name: str) -> str:
    if not uploaded_file:
        return None
    ensure_media_dir()
    user_safe = _sanitize_for_path(username)
    project_safe = _sanitize_for_path(project_name)
    user_dir = os.path.join(MEDIA_DIR, user_safe, project_safe)
    os.makedirs(user_dir, exist_ok=True)
    orig_name = getattr(uploaded_file, "name", "")
    _, ext = os.path.splitext(orig_name)
    ext = ext.lower() if ext else ""
    if not ext:
        typ = getattr(uploaded_file, "type", "") or ""
        ext = ".jpg" if "jpeg" in typ or "jpg" in typ else (".png" if "png" in typ else ".jpg")
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
        return path.replace("\", "/")
    except Exception:
        return None


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
# DB helpers
# ========================

def db_connect():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
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
                created_at TEXT
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
                photo_path TEXT
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

# ------------------------
# logging
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
# Migration (kept minimal)
# ========================

def migrate_from_json_if_needed():
    if os.path.exists(MIGRATION_MARKER):
        return
    if not os.path.exists(USERS_JSON):
        try:
            ensure_media_dir()
            with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
                f.write(f"no_users_json_at={datetime.now().isoformat()}
")
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
                f.write(f"empty_or_invalid_users_json_at={datetime.now().isoformat()}
")
        except Exception:
            pass
        return
    # perform light migration into sqlite
    init_db()
    with db_transaction() as conn:
        c = conn.cursor()
        for uname, info in users.items():
            pw = info.get("password") or ""
            if pw and len(pw) != 64:
                pw = hash_password(pw)
            role = info.get("role") or "Casting Director"
            last_login = info.get("last_login")
            try:
                c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?, ?, ?, ?)", (uname, pw or hash_password(""), role, last_login))
                user_id = c.lastrowid
            except sqlite3.IntegrityError:
                c.execute("SELECT id FROM users WHERE username=?", (uname,))
                row = c.fetchone()
                user_id = row["id"] if row else None
            if user_id:
                projects = info.get("projects", {}) or {}
                for pname, pblock in projects.items():
                    try:
                        c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?, ?, ?, ?)", (user_id, pname, pblock.get("description",""), pblock.get("created_at", datetime.now().isoformat())))
                        project_id = c.lastrowid
                    except sqlite3.IntegrityError:
                        c.execute("SELECT id FROM projects WHERE user_id=? AND name=?", (user_id, pname))
                        prow = c.fetchone()
                        project_id = prow["id"] if prow else None
                    if project_id:
                        for entrant in pblock.get("participants", []) or []:
                            c.execute("INSERT INTO participants (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                                project_id,
                                entrant.get("number"), entrant.get("name"), entrant.get("role"), entrant.get("age"), entrant.get("agency"), entrant.get("height"), entrant.get("waist"), entrant.get("dress_suit"), entrant.get("availability"), None
                            ))
    try:
        ensure_media_dir()
        with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
            f.write(f"migrated_at={datetime.now().isoformat()}
")
    except Exception:
        pass

# Initialize DB + migration
init_db()
migrate_from_json_if_needed()

# ========================
# Small DB helpers
# ========================

def get_user_by_username(conn, username):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    return c.fetchone()

def create_user(conn, username, password_hash, role="Casting Director"):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO users (username, password, role, last_login) VALUES (?, ?, ?, ?)", (username, password_hash, role, now))
    return c.lastrowid

def update_user_last_login(conn, user_id):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))

def list_projects_for_user(conn, user_id):
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY name COLLATE NOCASE", (user_id,))
    return c.fetchall()

def create_project(conn, user_id, name, description=""):
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO projects (user_id, name, description, created_at) VALUES (?, ?, ?, ?)", (user_id, name, description, now))
    return c.lastrowid

def get_project_by_name(conn, user_id, name):
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE user_id=? AND name=?", (user_id, name))
    return c.fetchone()

def delete_project_media(username, project_name):
    proj_media_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(username), _sanitize_for_path(project_name))
    try:
        if os.path.exists(proj_media_dir):
            shutil.rmtree(proj_media_dir)
    except Exception:
        pass

# ========================
# Session State Init
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
if "prefill_username" not in st.session_state:
    st.session_state["prefill_username"] = ""

# ========================
# Auth UI
# ========================
if not st.session_state["logged_in"]:
    st.markdown("# 🎬 Sacha's Casting Manager")
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
                st.experimental_rerun()
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
                st.experimental_rerun()
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
# Main App
# ========================
else:
    current_username = st.session_state["current_user"]
    try:
        conn_temp = db_connect()
        cur = conn_temp.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (current_username,))
        user_row = cur.fetchone()
        conn_temp.close()
    except Exception:
        user_row = None
    if not user_row:
        st.error("User not found. Log in again.")
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.experimental_rerun()

    user_id = user_row["id"]
    role = user_row["role"] or "Casting Director"

    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as **{current_username}**")
    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["current_project_name"] = None
        st.experimental_rerun()

    st.sidebar.subheader("Modes")
    st.session_state["participant_mode"] = st.sidebar.checkbox("Enable Participant Mode (Kiosk)", value=st.session_state.get("participant_mode", False))

    # ensure projects exist
    with db_connect() as conn:
        projects = list_projects_for_user(conn, user_id)
    if not projects:
        with db_transaction() as conn:
            create_project(conn, user_id, DEFAULT_PROJECT_NAME, "")
        with db_connect() as conn:
            projects = list_projects_for_user(conn, user_id)
    project_names = [p["name"] for p in projects]
    if st.session_state.get("current_project_name") not in project_names:
        st.session_state["current_project_name"] = project_names[0] if project_names else DEFAULT_PROJECT_NAME

    active = st.session_state["current_project_name"]
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    st.sidebar.write(f"**{active}**")

    if st.session_state["participant_mode"]:
        st.markdown("# 👋 Casting Check-In")
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
                conn.execute("INSERT INTO participants (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (pid, number, name, role_in, age, agency, height, waist, dress_suit, availability, photo_path))
                log_action(current_username, "participant_checkin", name)
            st.success("✅ Thanks for checking in!")
            st.experimental_rerun()

    else:
        st.markdown("# 🎬 Sacha's Casting Manager")
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
                if not p_name or not p_name.strip():
                    st.error("Please provide a project name.")
                else:
                    pname_clean = p_name.strip()
                    try:
                        with db_transaction() as conn:
                            existing = get_project_by_name(conn, user_id, pname_clean)
                            if existing:
                                st.error("A project with this name already exists.")
                            else:
                                create_project(conn, user_id, pname_clean, p_desc or "")
                                log_action(current_username, "create_project", pname_clean)
                                st.session_state["current_project_name"] = pname_clean
                                st.success(f"Project '{pname_clean}' created.")
                    except Exception as e:
                        st.error(f"Unable to create project: {e}")

        with db_connect() as conn:
            proj_rows = list_projects_for_user(conn, user_id)
        proj_items = []
        for r in proj_rows:
            project_id = r["id"]
            with db_connect() as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) as cnt FROM participants WHERE project_id=?", (project_id,))
                cnt = c.fetchone()["cnt"]
            proj_items.append((r["name"], r["description"], r["created_at"], cnt))

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
                st.experimental_rerun()
            if a2.button("Edit", key=f"editproj_{name}"):
                st.session_state["editing_project"] = name
                st.experimental_rerun()
            if a3.button("Delete", key=f"delproj_{name}"):
                st.session_state["confirm_delete_project"] = name
                st.experimental_rerun()

            # Inline Edit
            if st.session_state.get("editing_project") == name:
                with st.form(f"edit_project_form_{name}"):
                    new_name = st.text_input("Project Name", value=name)
                    new_desc = st.text_area("Description", value=desc, height=100)
                    c1, c2 = st.columns(2)
                    save_changes = c1.form_submit_button("Save")
                    cancel_edit = c2.form_submit_button("Cancel")
                if save_changes:
                    if not new_name.strip():
                        st.error("Name cannot be empty.")
                    else:
                        try:
                            with db_transaction() as conn:
                                proj = get_project_by_name(conn, user_id, name)
                                if not proj:
                                    st.error("Project not found.")
                                else:
                                    conn.execute("UPDATE projects SET name=?, description=? WHERE id=?", (new_name.strip(), new_desc, proj["id"]))
                                    log_action(current_username, "edit_project", f"{name} -> {new_name.strip()}")
                            st.success("Project updated.")
                            st.session_state["editing_project"] = None
                            if st.session_state.get("current_project_name") == name:
                                st.session_state["current_project_name"] = new_name.strip()
                            st.experimental_rerun()
                        except Exception as e:
                            st.error(f"Unable to save project: {e}")
                if cancel_edit:
                    st.session_state["editing_project"] = None
                    st.experimental_rerun()

            if st.session_state.get("confirm_delete_project") == name:
                st.warning(f"Type the project name **{name}** to confirm deletion. This cannot be undone.")
                with st.form(f"confirm_delete_{name}"):
                    confirm_text = st.text_input("Confirm name")
                    cc1, cc2 = st.columns(2)
                    do_delete = cc1.form_submit_button("Delete Permanently")
                    cancel_delete = cc2.form_submit_button("Cancel")
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
                                            try:
                                                os.remove(pf)
                                            except Exception:
                                                pass
                                    c.execute("DELETE FROM participants WHERE project_id=?", (pid,))
                                    c.execute("DELETE FROM projects WHERE id=?", (pid,))
                                    delete_project_media(current_username, name)
                                    log_action(current_username, "delete_project", name)
                            st.success(f"Project '{name}' deleted.")
                            st.session_state["confirm_delete_project"] = None
                            st.experimental_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete project: {e}")
                    else:
                        st.error("Project name mismatch. Not deleted.")
                if cancel_delete:
                    st.session_state["confirm_delete_project"] = None
                    st.experimental_rerun()

        # Participant management
        current = st.session_state.get("current_project_name", project_names[0] if 'project_names' in locals() and project_names else DEFAULT_PROJECT_NAME)
        with db_connect() as conn:
            proj = get_project_by_name(conn, user_id, current)
        if not proj:
            with db_transaction() as conn:
                create_project(conn, user_id, current, "")
            with db_connect() as conn:
                proj = get_project_by_name(conn, user_id, current)
        project_id = proj["id"]

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
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                submitted = st.form_submit_button("Add Participant")
            if submitted:
                try:
                    with db_transaction() as conn:
                        photo_path = save_photo_file(photo, current_username, current) if photo else None
                        conn.execute("INSERT INTO participants (project_id, number, name, role, age, agency, height, waist, dress_suit, availability, photo_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (project_id, number, pname, prole, page, pagency, pheight, pwaist, pdress, pavail, photo_path))
                        log_action(current_username, "add_participant", pname)
                    st.success("Participant added!")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Unable to add participant: {e}")

        # list participants with improved card look
        with db_connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM participants WHERE project_id=? ORDER BY id", (project_id,))
            participants = cur.fetchall()

        if not participants:
            st.info("No participants yet.")
        else:
            for p in participants:
                pid = p["id"]
                left_col, right_col = st.columns([5,1])
                # left: card (HTML for consistent styling)
                bytes_data = get_photo_bytes(p["photo_path"])
                if bytes_data:
                    try:
                        b64 = base64.b64encode(bytes_data).decode('utf-8')
                        img_tag = f"<img src='data:image/jpeg;base64,{b64}' alt='photo' />"
                    except Exception:
                        img_tag = "<div style='width:96px;height:96px;background:#f2f2f2;border-radius:8px;display:flex;align-items:center;justify-content:center'>Invalid</div>"
                else:
                    img_tag = "<div style='width:96px;height:96px;background:#f8f8f8;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#777'>No Photo</div>"

                info_html = f"<div class='participant-card'><div class='participant-photo'>{img_tag}</div><div class='participant-info'><div style='font-weight:600;font-size:1.05rem'>{(p['name'] or 'Unnamed')} <span style='color:#666;font-weight:400;font-size:0.9rem'>#{(p['number'] or '')}</span></div><div class='participant-meta'>Role: {(p['role'] or '')} • Age: {(p['age'] or '')} • Agency: {(p['agency'] or '')}</div><div class='participant-meta'>Height: {(p['height'] or '')} • Waist: {(p['waist'] or '')} • Dress/Suit: {(p['dress_suit'] or '')}</div><div class='participant-meta'>Availability: {(p['availability'] or '')}</div></div></div>"
                left_col.markdown(info_html, unsafe_allow_html=True)

                # right: actions (keep Edit/Delete functionality)
                if right_col.button("Edit", key=f"edit_{pid}"):
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
                                        try:
                                            os.remove(oldphoto)
                                        except Exception:
                                            pass
                                conn.execute("UPDATE participants SET number=?, name=?, role=?, age=?, agency=?, height=?, waist=?, dress_suit=?, availability=?, photo_path=? WHERE id=?", (enumber, ename, erole, eage, eagency, eheight, ewaist, edress, eavail, new_photo_path, pid))
                                log_action(current_username, "edit_participant", ename)
                            st.success("Participant updated!")
                            st.experimental_rerun()
                        except Exception as e:
                            st.error(f"Unable to save participant edits: {e}")
                    if cancel_edit:
                        st.experimental_rerun()

                if right_col.button("Delete", key=f"del_{pid}"):
                    try:
                        with db_transaction() as conn:
                            if isinstance(p["photo_path"], str) and os.path.exists(p["photo_path"]):
                                try:
                                    os.remove(p["photo_path"])
                                except Exception:
                                    pass
                            conn.execute("DELETE FROM participants WHERE id=?", (pid,))
                            log_action(current_username, "delete_participant", p["name"] or "")
                        st.warning("Participant deleted")
                        st.experimental_rerun()
                    except Exception as e:
                        st.error(f"Unable to delete participant: {e}")

        # Export to Word (unchanged)
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
                        doc = Document()
                        doc.add_heading(f"Participants - {current}", 0)
                        for p in parts:
                            table = doc.add_table(rows=1, cols=2)
                            table.autofit = False
                            table.columns[0].width = Inches(1.7)
                            table.columns[1].width = Inches(4.5)
                            row_cells = table.rows[0].cells

                            bytes_data = get_photo_bytes(p["photo_path"])
                            if bytes_data:
                                try:
                                    image_stream = io.BytesIO(bytes_data)
                                    image_stream.seek(0)
                                    paragraph = row_cells[0].paragraphs[0]
                                    run = paragraph.add_run()
                                    run.add_picture(image_stream, width=Inches(1.5))
                                except Exception:
                                    row_cells[0].text = "Photo Error"
                            else:
                                row_cells[0].text = "No Photo"

                            info_text = (
                                f"Number: {p.get('number','')}
"
                                f"Name: {p.get('name','')}
"
                                f"Role: {p.get('role','')}
"
                                f"Age: {p.get('age','')}
"
                                f"Agency: {p.get('agency','')}
"
                                f"Height: {p.get('height','')}
"
                                f"Waist: {p.get('waist','')}
"
                                f"Dress/Suit: {p.get('dress_suit','')}
"
                                f"Next Available: {p.get('availability','')}"
                            )
                            row_cells[1].text = info_text
                            doc.add_paragraph("
")

                        word_stream = io.BytesIO()
                        doc.save(word_stream)
                        word_stream.seek(0)
                        st.download_button(
                            label="Click to download Word file",
                            data=word_stream,
                            file_name=f"{current}_participants.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )

        # Admin dashboard (kept minimal)
        if role == "Admin":
            st.header("👑 Admin Dashboard")
            if st.button("🔄 Refresh Users"):
                st.experimental_rerun()

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
                        st.experimental_rerun()
                    except Exception as e:
                        st.error(f"Unable to change role: {e}")

                if a2.button("Delete", key=f"deluser_{uname}"):
                    if uname == "admin":
                        st.error("Cannot delete built-in admin.")
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
                                            try:
                                                os.remove(pf)
                                            except Exception:
                                                pass
                                    cur.execute("DELETE FROM participants WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                                    cur.execute("DELETE FROM projects WHERE user_id=?", (uid,))
                                    cur.execute("DELETE FROM users WHERE id=?", (uid,))
                                    log_action(current_username, "delete_user", uname)
                            st.warning(f"User {uname} deleted.")
                            st.experimental_rerun()
                        except Exception as e:
                            st.error(f"Unable to delete user: {e}")

