# sachas_casting_manager.py
import streamlit as st
import json
import os
import io
import base64
import time
import sys
import uuid
import shutil
import re
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image, UnidentifiedImageError
import hashlib

# ========================
# Page Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")

# ========================
# Constants
# ========================
USERS_FILE = "users.json"
LOG_FILE = "logs.json"
DEFAULT_PROJECT_NAME = "Default Project"
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".migrated_v1")

# Lock settings
LOCK_STALE_SECONDS = 30  # consider lock stale after this many seconds
LOCK_RETRY_DELAY = 0.08  # seconds between lock attempts
LOCK_ACQUIRE_TIMEOUT = 5  # seconds to retry before giving up

# ========================
# File Lock Helpers
# ========================
def _lockfile_name(filename):
    return f"{filename}.lock"

def acquire_file_lock(filename, timeout=LOCK_ACQUIRE_TIMEOUT):
    lockfile = _lockfile_name(filename)
    start = time.time()
    while True:
        try:
            fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()}\n{time.time()}\n")
            return True
        except FileExistsError:
            try:
                mtime = os.path.getmtime(lockfile)
                age = time.time() - mtime
                if age > LOCK_STALE_SECONDS:
                    try:
                        os.remove(lockfile)
                    except Exception:
                        pass
                else:
                    if time.time() - start > timeout:
                        raise TimeoutError(f"Timeout acquiring lock for {filename}")
                    time.sleep(LOCK_RETRY_DELAY)
            except FileNotFoundError:
                continue
        except Exception as e:
            raise

def release_file_lock(filename):
    lockfile = _lockfile_name(filename)
    try:
        if os.path.exists(lockfile):
            os.remove(lockfile)
    except Exception:
        pass

def wait_for_no_lock(filename, timeout=LOCK_ACQUIRE_TIMEOUT):
    lockfile = _lockfile_name(filename)
    start = time.time()
    while os.path.exists(lockfile):
        try:
            mtime = os.path.getmtime(lockfile)
            if time.time() - mtime > LOCK_STALE_SECONDS:
                try:
                    os.remove(lockfile)
                    return
                except Exception:
                    pass
        except FileNotFoundError:
            return
        if time.time() - start > timeout:
            raise TimeoutError(f"Timeout waiting for lock to clear: {filename}")
        time.sleep(LOCK_RETRY_DELAY)

# ========================
# JSON Helpers (with locks for writes)
# ========================
def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default
        wait_for_no_lock(filename)
        with open(filename, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return default
    except TimeoutError:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    except Exception:
        return default

def save_json(filename, data):
    try:
        acquire_file_lock(filename)
    except TimeoutError:
        raise
    try:
        tmp_name = f"{filename}.tmp"
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, filename)
    finally:
        release_file_lock(filename)

def load_users():
    return load_json(USERS_FILE, {})

def save_users(users):
    save_json(USERS_FILE, users)

def load_logs():
    return load_json(LOG_FILE, [])

def save_logs(logs):
    save_json(LOG_FILE, logs)

# ========================
# Other Helpers
# ========================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def _default_project_block():
    return {
        "description": "",
        "created_at": datetime.now().isoformat(),
        "participants": []
    }

def log_action(user, action, details=""):
    for attempt in range(2):
        try:
            logs = load_logs()
            logs.append({
                "timestamp": datetime.now().isoformat(),
                "user": user,
                "action": action,
                "details": details
            })
            save_logs(logs)
            return
        except TimeoutError:
            time.sleep(0.05)
    # best-effort

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# sanitize strings for filesystem paths
def _sanitize_for_path(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return re.sub(r"[^0-9A-Za-z\-_]+", "_", s)

# save uploaded file bytes to media/<user>/<project>/<uuid>.<ext>
def save_photo_file(uploaded_file, username: str, project_name: str) -> str:
    if not uploaded_file:
        return None
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
        return path.replace("\\", "/")
    except Exception:
        return None

# save raw bytes to media path (used for migrating base64)
def save_photo_bytes(bytes_data: bytes, username: str, project_name: str, ext_hint: str = None) -> str:
    if not bytes_data:
        return None
    user_safe = _sanitize_for_path(username)
    project_safe = _sanitize_for_path(project_name)
    user_dir = os.path.join(MEDIA_DIR, user_safe, project_safe)
    os.makedirs(user_dir, exist_ok=True)
    # detect image format using Pillow
    ext = ".jpg"
    try:
        buf = io.BytesIO(bytes_data)
        buf.seek(0)
        img = Image.open(buf)
        fmt = (img.format or "").lower()
        if fmt in ["jpeg", "jpg"]:
            ext = ".jpg"
        elif fmt == "png":
            ext = ".png"
        elif fmt == "gif":
            ext = ".gif"
        elif fmt == "webp":
            ext = ".webp"
        else:
            if ext_hint:
                ext = ext_hint
            else:
                ext = ".jpg"
    except UnidentifiedImageError:
        # fallback to hint or jpg
        ext = ext_hint or ".jpg"
    except Exception:
        ext = ext_hint or ".jpg"

    filename = f"{uuid.uuid4().hex}{ext if ext.startswith('.') else '.'+ext}"
    path = os.path.join(user_dir, filename)
    try:
        with open(path, "wb") as f:
            f.write(bytes_data)
            f.flush()
            os.fsync(f.fileno())
        return path.replace("\\", "/")
    except Exception:
        return None

def remove_media_file(path: str):
    try:
        if not path:
            return
        if isinstance(path, str) and os.path.exists(path) and os.path.commonpath([os.path.abspath(path), os.path.abspath(MEDIA_DIR)]) == os.path.abspath(MEDIA_DIR):
            os.remove(path)
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

def photo_to_b64(file):
    if not file:
        return None
    try:
        try:
            file.seek(0)
        except Exception:
            pass
        data = file.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return base64.b64encode(data).decode("utf-8")
    except Exception:
        return None

def b64_to_photo(b64_string):
    if not b64_string:
        return None
    try:
        return base64.b64decode(b64_string)
    except Exception:
        return None

# ========================
# Migration: move existing base64 images to media/ and update users.json
# ========================
def looks_like_base64_image(s: str) -> bool:
    if not isinstance(s, str):
        return False
    # small heuristic: long string (>120 chars), contains only base64 chars and maybe padding, and not a filesystem path
    if len(s) < 120:
        return False
    if os.path.exists(s):
        return False
    # base64 regex (allow newlines)
    if re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", s):
        return True
    return False

def migrate_base64_photos_once():
    # if migration marker exists, skip
    try:
        if os.path.exists(MIGRATION_MARKER):
            return
    except Exception:
        pass

    users = load_users()
    changed = False

    for uname, info in list(users.items()):
        if not isinstance(info, dict):
            continue
        projects = info.get("projects", {})
        if not isinstance(projects, dict):
            continue
        for pname, pblock in list(projects.items()):
            participants = pblock.get("participants", [])
            if not isinstance(participants, list):
                continue
            for idx, entrant in enumerate(participants):
                photo_field = entrant.get("photo")
                # if already a filesystem path, skip
                if isinstance(photo_field, str) and os.path.exists(photo_field):
                    continue
                # if looks like base64, attempt decode & save
                if looks_like_base64_image(photo_field):
                    try:
                        bytes_data = base64.b64decode(photo_field)
                        # detect extension via Pillow
                        ext_hint = ".jpg"
                        try:
                            buf = io.BytesIO(bytes_data)
                            buf.seek(0)
                            img = Image.open(buf)
                            fmt = (img.format or "").lower()
                            if fmt in ["jpeg", "jpg"]:
                                ext_hint = ".jpg"
                            elif fmt == "png":
                                ext_hint = ".png"
                            elif fmt == "gif":
                                ext_hint = ".gif"
                            elif fmt == "webp":
                                ext_hint = ".webp"
                        except Exception:
                            pass
                        new_path = save_photo_bytes(bytes_data, uname, pname, ext_hint)
                        if new_path:
                            users[uname]["projects"][pname]["participants"][idx]["photo"] = new_path
                            changed = True
                    except Exception:
                        # if decode fails, skip
                        continue

    if changed:
        # save users under lock
        try:
            save_users(users)
        except TimeoutError:
            # if unable to save, don't crash; try later
            pass

    # create marker file to avoid re-running
    try:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
            f.write(f"migrated_at={datetime.now().isoformat()}\n")
    except Exception:
        pass

# Run migration at startup (best-effort)
try:
    migrate_base64_photos_once()
except Exception:
    # don't let migration crash the app
    pass

# ========================
# Session State Init
# ========================
if "page" not in st.session_state:
    st.session_state["page"] = "login"
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "current_project" not in st.session_state:
    st.session_state["current_project"] = None
if "participant_mode" not in st.session_state:
    st.session_state["participant_mode"] = False
if "confirm_delete_project" not in st.session_state:
    st.session_state["confirm_delete_project"] = None
if "editing_project" not in st.session_state:
    st.session_state["editing_project"] = None
if "editing_participant" not in st.session_state:
    st.session_state["editing_participant"] = None

# initial load
users = load_users()

# ========================
# Auth Screens
# ========================
if not st.session_state["logged_in"]:
    st.title("🎬 Sacha's Casting Manager")

    choice = st.radio("Choose an option", ["Login", "Sign Up"], horizontal=True)

    if choice == "Login":
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")

        if login_btn:
            # Built-in Admin backdoor
            if username == "admin" and password == "supersecret":
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = "admin"
                users = load_users()
                users["admin"] = users.get("admin", {})
                users["admin"]["password"] = hash_password(password)
                users["admin"]["role"] = "Admin"
                users["admin"]["last_login"] = datetime.now().isoformat()
                users["admin"]["projects"] = users["admin"].get("projects", {DEFAULT_PROJECT_NAME: _default_project_block()})
                try:
                    save_users(users)
                except TimeoutError:
                    st.warning("Couldn't save admin login to disk due to file lock; proceeding anyway.")
                log_action("admin", "login")
                st.success("Logged in as Admin ✅")
                safe_rerun()

            users = load_users()
            if username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                if "projects" not in users[username] or not isinstance(users[username]["projects"], dict):
                    users[username]["projects"] = {DEFAULT_PROJECT_NAME: _default_project_block()}
                try:
                    save_users(users)
                except TimeoutError:
                    st.warning("Couldn't persist login time due to file lock; logging in anyway.")
                log_action(username, "login")
                st.success(f"Welcome back {username}!")
                safe_rerun()
            else:
                st.error("Invalid credentials")

    else:  # Sign Up
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        role = st.selectbox("Role", ["Casting Director", "Assistant"])
        signup_btn = st.button("Sign Up")

        if signup_btn:
            users = load_users()
            if not new_user or not new_pass:
                st.error("Please provide a username and password.")
            elif new_user in users:
                st.error("Username already exists")
            else:
                users[new_user] = {
                    "password": hash_password(new_pass),
                    "role": role,
                    "last_login": datetime.now().isoformat(),
                    "projects": {DEFAULT_PROJECT_NAME: _default_project_block()}
                }
                try:
                    save_users(users)
                except TimeoutError:
                    st.error("Unable to create account right now (file locked). Please try again shortly.")
                else:
                    st.success("Account created! Please log in.")
                    safe_rerun()

# ========================
# Main App (after login)
# ========================
else:
    users = load_users()
    current_user = st.session_state["current_user"]

    if current_user not in users:
        st.error("User not found. Please log in again.")
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        safe_rerun()

    user_data = users[current_user]
    projects = user_data.get("projects", {})
    if not isinstance(projects, dict) or not projects:
        projects = {DEFAULT_PROJECT_NAME: _default_project_block()}
        user_data["projects"] = projects
        users[current_user] = user_data
        try:
            save_users(users)
        except TimeoutError:
            st.warning("Couldn't persist default project due to file lock; it will be created in memory.")

    # Sidebar
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")

    role = user_data.get("role", "Casting Director")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        st.session_state["current_project"] = None
        safe_rerun()

    # Modes
    st.sidebar.subheader("Modes")
    try:
        st.session_state["participant_mode"] = st.sidebar.toggle(
            "Enable Participant Mode (Kiosk)",
            value=st.session_state.get("participant_mode", False)
        )
    except Exception:
        st.session_state["participant_mode"] = st.sidebar.checkbox(
            "Enable Participant Mode (Kiosk)",
            value=st.session_state.get("participant_mode", False)
        )

    # Active Project Display in Sidebar
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    if st.session_state.get("current_project") not in projects:
        st.session_state["current_project"] = next(iter(projects.keys()))
    active = st.session_state.get("current_project", DEFAULT_PROJECT_NAME)
    st.sidebar.write(f"**{active}**")

    # ===== Participant Mode (Kiosk) =====
    if st.session_state["participant_mode"]:
        st.title("👋 Welcome to Casting Check-In")
        st.caption("Please fill in your details below. Your information will be saved to the currently active project.")
        st.info(f"Submitting to project: **{active}**")

        with st.form("participant_form"):
            number = st.text_input("Number")
            name = st.text_input("Name")
            role_input = st.text_input("Role")
            age = st.text_input("Age")
            agency = st.text_input("Agency")
            height = st.text_input("Height")
            waist = st.text_input("Waist")
            dress_suit = st.text_input("Dress/Suit")
            availability = st.text_input("Next Availability")
            photo = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png"])
            submitted = st.form_submit_button("Submit")

            if submitted:
                users = load_users()
                if current_user not in users:
                    st.error("User not found. Please log in again.")
                    safe_rerun()
                user_data = users[current_user]
                projects = user_data.get("projects", {})
                proj_block = projects.get(active, _default_project_block())
                participants = proj_block.get("participants", [])

                photo_path = save_photo_file(photo, current_user, active) if photo else None

                entry = {
                    "number": number,
                    "name": name,
                    "role": role_input,
                    "age": age,
                    "agency": agency,
                    "height": height,
                    "waist": waist,
                    "dress_suit": dress_suit,
                    "availability": availability,
                    "photo": photo_path
                }
                participants.append(entry)
                proj_block["participants"] = participants
                projects[active] = proj_block
                user_data["projects"] = projects
                users[current_user] = user_data
                try:
                    save_users(users)
                except TimeoutError:
                    st.error("Unable to save participant right now (file locked). Please try again shortly.")
                else:
                    st.success("✅ Thanks for checking in! Next participant may proceed.")
                    log_action(current_user, "participant_checkin", name)
                    safe_rerun()

    # ===== Casting Manager Mode =====
    else:
        st.title("🎬 Sacha's Casting Manager")

        # ------------------------
        # Project Manager
        # ------------------------
        st.header("📁 Project Manager")
        pm_col1, pm_col2 = st.columns([3, 2])
        with pm_col1:
            query = st.text_input("Search projects by name or description")
        with pm_col2:
            sort_opt = st.selectbox(
                "Sort by",
                ["Name A→Z", "Newest", "Oldest", "Most Participants", "Fewest Participants"],
                index=0
            )

        # Create Project
        with st.expander("➕ Create New Project", expanded=False):
            with st.form("new_project_form"):
                p_name = st.text_input("Project Name")
                p_desc = st.text_area("Description", height=80)
                create_btn = st.form_submit_button("Create Project")
                if create_btn:
                    users = load_users()
                    if current_user not in users:
                        st.error("User not found. Please log in again.")
                        safe_rerun()
                    user_data = users[current_user]
                    projects = user_data.get("projects", {})

                    if not p_name:
                        st.error("Please provide a project name.")
                    elif p_name in projects:
                        st.error("A project with this name already exists.")
                    else:
                        projects[p_name] = {
                            "description": p_desc or "",
                            "created_at": datetime.now().isoformat(),
                            "participants": []
                        }
                        user_data["projects"] = projects
                        users[current_user] = user_data
                        try:
                            save_users(users)
                        except TimeoutError:
                            st.error("Unable to create project right now (file locked). Please try again shortly.")
                        else:
                            log_action(current_user, "create_project", p_name)
                            st.success(f"Project '{p_name}' created.")
                            st.session_state["current_project"] = p_name
                            safe_rerun()

        # Project list rendering
        def proj_meta_tuple(name, block):
            count = len(block.get("participants", []))
            created = block.get("created_at", datetime.now().isoformat())
            return name, block.get("description", ""), created, count

        users = load_users()
        user_data = users.get(current_user, user_data)
        projects = user_data.get("projects", {})
        proj_items = [proj_meta_tuple(n, b) for n, b in projects.items()]
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

        # Render header row
        hdr = st.columns([3, 4, 2, 2, 4])
        hdr[0].markdown("**Project**")
        hdr[1].markdown("**Description**")
        hdr[2].markdown("**Created**")
        hdr[3].markdown("**Participants**")
        hdr[4].markdown("**Actions**")

        # Cards/rows
        for name, desc, created, count in proj_items:
            is_active = (name == st.session_state["current_project"])
            cols = st.columns([3, 4, 2, 2, 4])
            cols[0].markdown(f"{'🟢 ' if is_active else ''}**{name}**")
            cols[1].markdown(desc or "—")
            cols[2].markdown(created.split("T")[0])
            cols[3].markdown(str(count))

            a1, a2, a3 = cols[4].columns([1, 1, 1])
            if a1.button("Set Active", key=f"setactive_{name}"):
                st.session_state["current_project"] = name
                safe_rerun()
            if a2.button("Edit", key=f"editproj_{name}"):
                st.session_state["editing_project"] = name
                safe_rerun()
            if a3.button("Delete", key=f"delproj_{name}"):
                st.session_state["confirm_delete_project"] = name
                safe_rerun()

            # Inline Edit
            if st.session_state.get("editing_project") == name:
                with st.form(f"edit_project_form_{name}"):
                    new_name = st.text_input("Project Name", value=name)
                    new_desc = st.text_area("Description", value=desc, height=100)
                    c1, c2 = st.columns(2)
                    save_changes = c1.form_submit_button("Save")
                    cancel_edit = c2.form_submit_button("Cancel")

                    if save_changes:
                        users = load_users()
                        if current_user not in users:
                            st.error("User not found. Please log in again.")
                            safe_rerun()
                        user_data = users[current_user]
                        projects = user_data.get("projects", {})

                        if not new_name:
                            st.error("Name cannot be empty.")
                        elif new_name != name and new_name in projects:
                            st.error("Another project already has this name.")
                        else:
                            block = projects.pop(name)
                            block["description"] = new_desc
                            # move media files from old project folder to new project folder if any
                            old_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(current_user), _sanitize_for_path(name))
                            new_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(current_user), _sanitize_for_path(new_name))
                            try:
                                if os.path.exists(old_dir):
                                    os.makedirs(new_dir, exist_ok=True)
                                    for f in os.listdir(old_dir):
                                        oldpath = os.path.join(old_dir, f)
                                        newpath = os.path.join(new_dir, f)
                                        try:
                                            shutil.move(oldpath, newpath)
                                        except Exception:
                                            pass
                                    try:
                                        if not os.listdir(old_dir):
                                            os.rmdir(old_dir)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                            projects[new_name] = block
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = new_name
                            user_data["projects"] = projects
                            users[current_user] = user_data
                            try:
                                save_users(users)
                            except TimeoutError:
                                st.error("Unable to save project changes (file locked). Please try again shortly.")
                            else:
                                log_action(current_user, "edit_project", f"{name} -> {new_name}")
                                st.success("Project updated.")
                                st.session_state["editing_project"] = None
                                safe_rerun()
                    if cancel_edit:
                        st.session_state["editing_project"] = None
                        safe_rerun()

            # Delete confirmation
            if st.session_state.get("confirm_delete_project") == name:
                st.warning(f"Type the project name **{name}** to confirm deletion. This cannot be undone.")
                with st.form(f"confirm_delete_{name}"):
                    confirm_text = st.text_input("Confirm name")
                    cc1, cc2 = st.columns(2)
                    do_delete = cc1.form_submit_button("Delete Permanently")
                    cancel_delete = cc2.form_submit_button("Cancel")
                if do_delete:
                    if confirm_text == name:
                        users = load_users()
                        if current_user not in users:
                            st.error("User not found. Please log in again.")
                            safe_rerun()
                        user_data = users[current_user]
                        projects = user_data.get("projects", {})
                        if len(projects) <= 1:
                            st.error("You must keep at least one project.")
                        else:
                            # remove media folder for this project
                            proj_media_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(current_user), _sanitize_for_path(name))
                            try:
                                if os.path.exists(proj_media_dir):
                                    shutil.rmtree(proj_media_dir)
                            except Exception:
                                pass

                            projects.pop(name, None)
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = next(iter(projects.keys()))
                            user_data["projects"] = projects
                            users[current_user] = user_data
                            try:
                                save_users(users)
                            except TimeoutError:
                                st.error("Unable to delete project (file locked). Please try again shortly.")
                            else:
                                log_action(current_user, "delete_project", name)
                                st.success(f"Project '{name}' deleted.")
                                st.session_state["confirm_delete_project"] = None
                                safe_rerun()
                    else:
                        st.error("Project name mismatch. Not deleted.")
                if cancel_delete:
                    st.session_state["confirm_delete_project"] = None
                    safe_rerun()

        # ------------------------
        # Participant Management
        # ------------------------
        current = st.session_state["current_project"]
        users = load_users()
        if current_user not in users:
            st.error("User not found. Please log in again.")
            safe_rerun()
        user_data = users[current_user]
        projects = user_data.get("projects", {})
        proj_block = projects.get(current, _default_project_block())
        project_data = proj_block.get("participants", [])

        st.header(f"👥 Participants — {current}")

        # Add new participant
        with st.expander("➕ Add New Participant"):
            with st.form("add_participant"):
                number = st.text_input("Number")
                name = st.text_input("Name")
                role_input = st.text_input("Role")
                age = st.text_input("Age")
                agency = st.text_input("Agency")
                height = st.text_input("Height")
                waist = st.text_input("Waist")
                dress_suit = st.text_input("Dress/Suit")
                availability = st.text_input("Next Availability")
                photo = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png"])
                submitted = st.form_submit_button("Add Participant")

                if submitted:
                    photo_path = save_photo_file(photo, current_user, current) if photo else None
                    entry = {
                        "number": number,
                        "name": name,
                        "role": role_input,
                        "age": age,
                        "agency": agency,
                        "height": height,
                        "waist": waist,
                        "dress_suit": dress_suit,
                        "availability": availability,
                        "photo": photo_path
                    }
                    project_data.append(entry)
                    projects[current]["participants"] = project_data
                    user_data["projects"] = projects
                    users[current_user] = user_data
                    try:
                        save_users(users)
                    except TimeoutError:
                        st.error("Unable to save participant right now (file locked). Please try again shortly.")
                    else:
                        st.success("Participant added!")
                        log_action(current_user, "add_participant", name)
                        safe_rerun()

        if not project_data:
            st.info("No participants yet.")
        else:
            for idx, p in enumerate(project_data):
                with st.container():
                    cols = st.columns([1, 2, 1, 2])
                    bytes_data = get_photo_bytes(p.get("photo"))
                    if bytes_data:
                        try:
                            buf = io.BytesIO(bytes_data)
                            buf.seek(0)
                            img = Image.open(buf)
                            try:
                                img = img.convert("RGB")
                            except Exception:
                                pass
                            cols[0].image(img, width=100)
                        except Exception:
                            cols[0].write("Invalid Photo")
                    else:
                        cols[0].write("No Photo")

                    cols[1].markdown(
                        f"**{p.get('name','Unnamed')}** (#{p.get('number','')})  \n"
                        f"Role: {p.get('role','')} | Age: {p.get('age','')}  \n"
                        f"Agency: {p.get('agency','')}  \n"
                        f"Height: {p.get('height','')} | Waist: {p.get('waist','')} | Dress/Suit: {p.get('dress_suit','')}  \n"
                        f"Availability: {p.get('availability','')}"
                    )

                    e_btn, d_btn = cols[2], cols[3]

                    # Edit participant
                    if e_btn.button("Edit", key=f"edit_{idx}"):
                        with st.form(f"edit_participant_{idx}"):
                            enumber = st.text_input("Number", value=p.get("number",""))
                            ename = st.text_input("Name", value=p.get("name",""))
                            erole = st.text_input("Role", value=p.get("role",""))
                            eage = st.text_input("Age", value=p.get("age",""))
                            eagency = st.text_input("Agency", value=p.get("agency",""))
                            eheight = st.text_input("Height", value=p.get("height",""))
                            ewaist = st.text_input("Waist", value=p.get("waist",""))
                            edress_suit = st.text_input("Dress/Suit", value=p.get("dress_suit",""))
                            eavailability = st.text_input("Next Availability", value=p.get("availability",""))
                            ephoto = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                            save_edit = st.form_submit_button("Save Changes")
                            cancel_edit = st.form_submit_button("Cancel")
                            if save_edit:
                                new_photo_path = p.get("photo")
                                if ephoto:
                                    new_photo_path = save_photo_file(ephoto, current_user, current)
                                    old_photo = p.get("photo")
                                    if isinstance(old_photo, str) and os.path.exists(old_photo):
                                        remove_media_file(old_photo)

                                p.update({
                                    "number": enumber,
                                    "name": ename,
                                    "role": erole,
                                    "age": eage,
                                    "agency": eagency,
                                    "height": eheight,
                                    "waist": ewaist,
                                    "dress_suit": edress_suit,
                                    "availability": eavailability,
                                    "photo": new_photo_path
                                })
                                projects[current]["participants"] = project_data
                                user_data["projects"] = projects
                                users[current_user] = user_data
                                try:
                                    save_users(users)
                                except TimeoutError:
                                    st.error("Unable to save participant edits (file locked). Please try again shortly.")
                                else:
                                    st.success("Participant updated!")
                                    log_action(current_user, "edit_participant", ename)
                                    safe_rerun()
                            if cancel_edit:
                                safe_rerun()

                    # Delete participant
                    if d_btn.button("Delete", key=f"del_{idx}"):
                        pf = p.get("photo")
                        if isinstance(pf, str) and os.path.exists(pf):
                            remove_media_file(pf)
                        project_data.pop(idx)
                        projects[current]["participants"] = project_data
                        user_data["projects"] = projects
                        users[current_user] = user_data
                        try:
                            save_users(users)
                        except TimeoutError:
                            st.error("Unable to delete participant (file locked). Please try again shortly.")
                        else:
                            st.warning("Participant deleted")
                            log_action(current_user, "delete_participant", p.get("name",""))
                            safe_rerun()

        # ------------------------
        # Export Participants to Word
        # ------------------------
        st.subheader("📄 Export Participants (Word)")
        if st.button("Download Word File of Current Project"):
            if project_data:
                doc = Document()
                doc.add_heading(f"Participants - {current}", 0)
                for p in project_data:
                    table = doc.add_table(rows=1, cols=2)
                    table.autofit = False
                    table.columns[0].width = Inches(1.7)
                    table.columns[1].width = Inches(4.5)
                    row_cells = table.rows[0].cells

                    bytes_data = get_photo_bytes(p.get("photo"))
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
                        f"Number: {p.get('number','')}\n"
                        f"Name: {p.get('name','')}\n"
                        f"Role: {p.get('role','')}\n"
                        f"Age: {p.get('age','')}\n"
                        f"Agency: {p.get('agency','')}\n"
                        f"Height: {p.get('height','')}\n"
                        f"Waist: {p.get('waist','')}\n"
                        f"Dress/Suit: {p.get('dress_suit','')}\n"
                        f"Next Available: {p.get('availability','')}"
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
            else:
                st.info("No participants in this project yet.")

        # ------------------------
        # Admin Dashboard (User Account Management)
        # ------------------------
        if role == "Admin":
            st.header("👑 Admin Dashboard")

            if st.button("🔄 Refresh Users"):
                safe_rerun()

            admin_users = load_users()  # fresh

            ucol1, ucol2 = st.columns([3, 2])
            with ucol1:
                uquery = st.text_input("Search accounts by username or role")
            with ucol2:
                urole_filter = st.selectbox("Filter role", ["All", "Admin", "Casting Director", "Assistant"], index=0)

            # Header row
            uhdr = st.columns([3, 2, 3, 3, 4])
            uhdr[0].markdown("**Username**")
            uhdr[1].markdown("**Role**")
            uhdr[2].markdown("**Last Login**")
            uhdr[3].markdown("**Projects**")
            uhdr[4].markdown("**Actions**")

            items = []
            for u, info in admin_users.items():
                if not isinstance(info, dict):
                    continue
                if uquery and uquery.lower() not in u.lower() and uquery.lower() not in info.get("role","").lower():
                    continue
                if urole_filter != "All" and info.get("role","") != urole_filter:
                    continue
                projnames = ", ".join(info.get("projects", {}).keys()) if isinstance(info.get("projects", {}), dict) else ""
                items.append((u, info.get("role",""), info.get("last_login",""), projnames))

            for uname, urole, last, projlist in items:
                cols = st.columns([3, 2, 3, 3, 4])
                cols[0].markdown(f"**{uname}**")
                role_sel = cols[1].selectbox(
                    "role_sel_" + uname,
                    ["Admin", "Casting Director", "Assistant"],
                    index=["Admin","Casting Director","Assistant"].index(urole) if urole in ["Admin","Casting Director","Assistant"] else 1,
                    key=f"role_sel_{uname}"
                )
                cols[2].markdown(last or "—")
                cols[3].markdown(projlist or "—")

                a1, a2 = cols[4].columns([1,1])
                if a1.button("Save Role", key=f"saverole_{uname}"):
                    admin_users = load_users()
                    if uname not in admin_users:
                        st.error("User not found.")
                        safe_rerun()
                    if uname == "admin" and role_sel != "Admin":
                        st.error("Built-in admin must remain Admin.")
                    else:
                        admin_users[uname]["role"] = role_sel
                        try:
                            save_users(admin_users)
                        except TimeoutError:
                            st.error("Unable to change role (file locked). Please try again shortly.")
                        else:
                            log_action(current_user, "change_role", f"{uname} -> {role_sel}")
                            st.success(f"Role updated for {uname}.")
                            safe_rerun()

                if a2.button("Delete", key=f"deluser_{uname}"):
                    admin_users = load_users()
                    if uname not in admin_users:
                        st.error("User not found.")
                        safe_rerun()
                    if uname == "admin":
                        st.error("Cannot delete the built-in admin.")
                    else:
                        # remove user's media directory
                        user_media_dir = os.path.join(MEDIA_DIR, _sanitize_for_path(uname))
                        try:
                            if os.path.exists(user_media_dir):
                                shutil.rmtree(user_media_dir)
                        except Exception:
                            pass

                        admin_users.pop(uname, None)
                        try:
                            save_users(admin_users)
                        except TimeoutError:
                            st.error("Unable to delete user (file locked). Please try again shortly.")
                        else:
                            log_action(current_user, "delete_user", uname)
                            st.warning(f"User {uname} deleted.")
                            safe_rerun()
