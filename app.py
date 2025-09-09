import streamlit as st
import json, os, io, base64
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import hashlib

# ========================
# Files & Constants
# ========================
USERS_FILE = "users.json"
DATA_FILE = "casting_data.json"
LOG_FILE = "logs.json"
DEFAULT_PROJECT_NAME = "Default Project"

# ========================
# Helpers
# ========================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return default


def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


def load_users():
    return load_json(USERS_FILE, {})


def save_users(users):
    save_json(USERS_FILE, users)


def _default_project_block():
    return {
        "description": "",
        "created_at": datetime.now().isoformat(),
        "participants": []
    }


def load_data():
    users = load_users()
    data = {user: {"projects": {DEFAULT_PROJECT_NAME: _default_project_block()}} for user in users}
    raw = load_json(DATA_FILE, {})
    for user, pdata in raw.items():
        data[user] = pdata
    return data


def save_data(data):
    save_json(DATA_FILE, data)


def load_logs():
    return load_json(LOG_FILE, [])


def save_logs(logs):
    save_json(LOG_FILE, logs)


def log_action(user, action, details=""):
    logs = load_logs()
    logs.append({
        "timestamp": datetime.now().isoformat(),
        "user": user,
        "action": action,
        "details": details
    })
    save_logs(logs)


def photo_to_b64(file):
    return base64.b64encode(file.read()).decode("utf-8")


def b64_to_photo(b64_string):
    return base64.b64decode(b64_string)


def safe_rerun():
    try:
        st.experimental_rerun()
    except Exception:
        try:
            st.rerun()
        except Exception:
            pass

# ========================
# Session State Init
# ========================
for key in ["page", "logged_in", "current_user", "current_project", "participant_mode", "confirm_delete_project", "editing_project"]:
    if key not in st.session_state:
        st.session_state[key] = None if key == "current_user" else False

if st.session_state["current_project"] is None:
    st.session_state["current_project"] = DEFAULT_PROJECT_NAME

users = load_users()
data = load_data()

# ========================
# Auth Screens
# ========================
if not st.session_state["logged_in"]:
    st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")
    st.title("🎬 Sacha's Casting Manager")

    choice = st.radio("Choose an option", ["Login", "Sign Up"], horizontal=True)

    if choice == "Login":
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")

        if login_btn:
            if username == "admin" and password == "supersecret":
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = "admin"
                users["admin"] = {"password": hash_password(password), "role": "Admin", "last_login": datetime.now().isoformat(), "projects_accessed": []}
                save_users(users)
                log_action("admin", "login")
                safe_rerun()

            elif username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                save_users(users)
                log_action(username, "login")
                safe_rerun()
            else:
                st.error("Invalid credentials")

    else:
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        role = st.selectbox("Role", ["Casting Director", "Assistant"])
        signup_btn = st.button("Sign Up")

        if signup_btn:
            if not new_user or not new_pass:
                st.error("Please provide a username and password.")
            elif new_user in users:
                st.error("Username already exists")
            else:
                users[new_user] = {"password": hash_password(new_pass), "role": role, "last_login": datetime.now().isoformat(), "projects_accessed": {}}
                data[new_user] = {"projects": {DEFAULT_PROJECT_NAME: _default_project_block()}}
                save_users(users)
                save_data(data)
                st.success("Account created! Please log in.")
                safe_rerun()

# ========================
# Main App
# ========================
else:
    st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")
    current_user = st.session_state["current_user"]
    user_role = users[current_user].get("role", "Casting Director")

    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")
    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        safe_rerun()

    st.sidebar.subheader("Modes")
    st.session_state["participant_mode"] = st.sidebar.toggle("Participant Mode (Kiosk)", st.session_state["participant_mode"])
    st.sidebar.markdown("---")

    st.sidebar.subheader("Active Project")
    st.sidebar.write(f"**{st.session_state['current_project']}**")

    user_projects = data[current_user]["projects"]

    # ===== Participant Mode =====
    if st.session_state["participant_mode"]:
        st.title("👋 Welcome to Casting Check‑In")
        st.info(f"Submitting to project: **{st.session_state['current_project']}**")
        with st.form("participant_form"):
            fields = ["number", "name", "role", "age", "agency", "height", "waist", "dress_suit", "availability"]
            entries = {f: st.text_input(f.capitalize()) for f in fields}
            photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
            submitted = st.form_submit_button("Submit")
            if submitted:
                proj = user_projects.get(st.session_state['current_project'], _default_project_block())
                proj['participants'].append({**entries, "photo": photo_to_b64(photo) if photo else None})
                user_projects[st.session_state['current_project']] = proj
                save_data(data)
                st.success("✅ Checked in")
                log_action(current_user, "participant_checkin", entries.get("name", ""))

    # ===== Casting Manager Mode =====
    else:
        st.title("🎬 Project Manager")
        query = st.text_input("Search projects")
        sort_opt = st.selectbox("Sort by", ["Name A→Z","Newest","Oldest","Most Participants","Fewest Participants"])

        with st.expander("➕ Create New Project"):
            with st.form("new_project_form"):
                new_name = st.text_input("Project Name")
                new_desc = st.text_area("Description")
                create_btn = st.form_submit_button("Create")
                if create_btn:
                    if not new_name:
                        st.error("Enter project name")
                    elif new_name in user_projects:
                        st.error("Project already exists")
                    else:
                        user_projects[new_name] = _default_project_block()
                        user_projects[new_name]["description"] = new_desc
                        st.session_state['current_project'] = new_name
                        save_data(data)
                        log_action(current_user, "create_project", new_name)
                        safe_rerun()

        proj_items = [(n, b) for n, b in user_projects.items()]
        if query:
            proj_items = [x for x in proj_items if query.lower() in x[0].lower() or query.lower() in (x[1]['description'] or '').lower()]
        if sort_opt == "Name A→Z":
            proj_items.sort(key=lambda x: x[0].lower())
        elif sort_opt == "Newest":
            proj_items.sort(key=lambda x: x[1]['created_at'], reverse=True)
        elif sort_opt == "Oldest":
            proj_items.sort(key=lambda x: x[1]['created_at'])
        elif sort_opt == "Most Participants":
            proj_items.sort(key=lambda x: len(x[1]['participants']), reverse=True)
        elif sort_opt == "Fewest Participants":
            proj_items.sort(key=lambda x: len(x[1]['participants']))

        hdr = st.columns([3,4,2,3,4])
        hdr[0].markdown("**Project**")
        hdr[1].markdown("**Description**")
        hdr[2].markdown("**Created**")
        hdr[3].markdown("**Participants**")
        hdr[4].markdown("**Actions**")

        for name, block in proj_items:
            cols = st.columns([3,4,2,3,4])
            cols[0].markdown(f"{'🟢 ' if name==st.session_state['current_project'] else ''}**{name}**")
            cols[1].markdown(block['description'] or '—')
            cols[2].markdown(block['created_at'].split('T')[0])
            cols[3].markdown(str(len(block['participants'])))
            a1,a2,a3 = cols[4].columns([1,1,1])
            if a1.button("Set Active", key=f"set_{name}"):
                st.session_state['current_project'] = name
                safe_rerun()
            if a2.button("Delete", key=f"del_{name}"):
                user_projects.pop(name)
                if st.session_state['current_project']==name:
                    st.session_state['current_project'] = next(iter(user_projects.keys()))
                save_data(data)
                log_action(current_user, "delete_project", name)
                safe_rerun()
