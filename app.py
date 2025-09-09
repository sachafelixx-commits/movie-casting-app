import streamlit as st
import json, os, io, base64
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import hashlib

# ========================
# Page Config
# ========================
st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")

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
    raw = load_json(DATA_FILE, {})
    # Ensure each user has its own projects
    if "projects_per_user" not in raw:
        raw["projects_per_user"] = {}
    return raw

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
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
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
    st.session_state["current_project"] = DEFAULT_PROJECT_NAME
if "participant_mode" not in st.session_state:
    st.session_state["participant_mode"] = False
if "confirm_delete_project" not in st.session_state:
    st.session_state["confirm_delete_project"] = None
if "editing_project" not in st.session_state:
    st.session_state["editing_project"] = None
if "edit_participant_idx" not in st.session_state:
    st.session_state["edit_participant_idx"] = None

users = load_users()
data = load_data()

# Ensure active user has a project dict
current_user = st.session_state.get("current_user")
projects_per_user = data.get("projects_per_user", {})

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
            # Admin backdoor
            if username == "admin" and password == "supersecret":
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = "admin"
                users["admin"] = {
                    "password": hash_password(password),
                    "role": "Admin",
                    "last_login": datetime.now().isoformat(),
                    "projects_accessed": []
                }
                save_users(users)
                log_action("admin", "login")
                st.success("Logged in as Admin ✅")
                safe_rerun()

            users = load_users()
            if username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                save_users(users)
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
            if not new_user or not new_pass:
                st.error("Provide username and password.")
            elif new_user in users:
                st.error("Username already exists")
            else:
                users[new_user] = {
                    "password": hash_password(new_pass),
                    "role": role,
                    "last_login": datetime.now().isoformat(),
                    "projects_accessed": []
                }
                save_users(users)
                st.success("Account created! Please log in.")
                safe_rerun()

# ========================
# Main App
# ========================
else:
    current_user = st.session_state["current_user"]
    user_projects = projects_per_user.get(current_user, {})
    if not user_projects:
        user_projects[DEFAULT_PROJECT_NAME] = _default_project_block()
        user_projects = projects_per_user[current_user]

    # Sidebar
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")
    users = load_users()
    role = users.get(current_user, {}).get("role", "Casting Director")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        safe_rerun()

    st.sidebar.subheader("Modes")
    st.session_state["participant_mode"] = st.sidebar.toggle(
        "Enable Participant Mode (Kiosk)",
        value=st.session_state["participant_mode"]
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    active = st.session_state["current_project"]
    st.sidebar.write(f"**{active}**")

    projects = user_projects

    # ===== Participant Mode =====
    if st.session_state["participant_mode"]:
        st.title("👋 Welcome to Casting Check-In")
        st.caption("Fill in your details. Data goes to the active project.")
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
            photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
            submitted = st.form_submit_button("Submit")

            if submitted:
                proj_block = projects.get(active)
                if not proj_block:
                    projects[active] = _default_project_block()
                    proj_block = projects[active]
                participants = proj_block.get("participants", [])
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
                    "photo": photo_to_b64(photo) if photo else None
                }
                participants.append(entry)
                projects[active]["participants"] = participants
                projects_per_user[current_user] = projects
                data["projects_per_user"] = projects_per_user
                save_data(data)
                st.success("✅ Thanks for checking in!")
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
            query = st.text_input("Search projects")
        with pm_col2:
            sort_opt = st.selectbox(
                "Sort by",
                ["Name A→Z", "Newest", "Oldest", "Most Participants", "Fewest Participants"],
                index=0
            )

        # Create Project
        with st.expander("➕ Create New Project"):
            with st.form("new_project_form"):
                p_name = st.text_input("Project Name")
                p_desc = st.text_area("Description", height=80)
                create_btn = st.form_submit_button("Create Project")
                if create_btn:
                    if not p_name:
                        st.error("Provide project name.")
                    elif p_name in projects:
                        st.error("Project name exists.")
                    else:
                        projects[p_name] = {
                            "description": p_desc or "",
                            "created_at": datetime.now().isoformat(),
                            "participants": []
                        }
                        projects_per_user[current_user] = projects
                        data["projects_per_user"] = projects_per_user
                        save_data(data)
                        log_action(current_user, "create_project", p_name)
                        st.success(f"Project '{p_name}' created.")
                        st.session_state["current_project"] = p_name
                        safe_rerun()

        # Filter & sort projects
        def proj_meta_tuple(name, block):
            return name, block.get("description",""), block.get("created_at",datetime.now().isoformat()), len(block.get("participants",[]))

        proj_items = [proj_meta_tuple(n,b) for n,b in projects.items()]
        if query:
            q = query.lower().strip()
            proj_items = [x for x in proj_items if q in x[0].lower() or q in (x[1] or "").lower()]
        if sort_opt == "Name A→Z":
            proj_items.sort(key=lambda x:x[0].lower())
        elif sort_opt == "Newest":
            proj_items.sort(key=lambda x:x[2], reverse=True)
        elif sort_opt == "Oldest":
            proj_items.sort(key=lambda x:x[2])
        elif sort_opt == "Most Participants":
            proj_items.sort(key=lambda x:x[3], reverse=True)
        elif sort_opt == "Fewest Participants":
            proj_items.sort(key=lambda x:x[3])

        hdr = st.columns([3,4,2,2,4])
        hdr[0].markdown("**Project**")
        hdr[1].markdown("**Description**")
        hdr[2].markdown("**Created**")
        hdr[3].markdown("**Participants**")
        hdr[4].markdown("**Actions**")

        for name, desc, created, count in proj_items:
            is_active = (name == st.session_state["current_project"])
            cols = st.columns([3,4,2,2,4])
            cols[0].markdown(f"{'🟢 ' if is_active else ''}**{name}**")
            cols[1].markdown(desc or "—")
            cols[2].markdown(created.split("T")[0])
            cols[3].markdown(str(count))
            a1,a2,a3 = cols[4].columns([1,1,1])
            if a1.button("Set Active", key=f"setactive_{name}"):
                st.session_state["current_project"] = name
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
                            st.error("Name cannot be empty.")
                        elif new_name != name and new_name in projects:
                            st.error("Name exists.")
                        else:
                            block = projects.pop(name)
                            block["description"] = new_desc
                            projects[new_name] = block
                            st.session_state["current_project"] = new_name
                            projects_per_user[current_user] = projects
                            data["projects_per_user"] = projects_per_user
                            save_data(data)
                            log_action(current_user,"edit_project",f"{name}->{new_name}")
                            st.success("Project updated.")
                            st.session_state["editing_project"] = None
                            safe_rerun()
                    if cancel_edit:
                        st.session_state["editing_project"] = None
                        safe_rerun()

            # Delete confirmation
            if st.session_state.get("confirm_delete_project") == name:
                st.warning(f"Type project name **{name}** to confirm deletion")
                with st.form(f"confirm_delete_{name}"):
                    confirm_text = st.text_input("Confirm name")
                    cc1, cc2 = st.columns(2)
                    do_delete = cc1.form_submit_button("Delete")
                    cancel_delete = cc2.form_submit_button("Cancel")
                    if do_delete:
                        if confirm_text == name:
                            if len(projects)<=1:
                                st.error("Keep at least one project")
                            else:
                                projects.pop(name)
                                st.session_state["current_project"] = next(iter(projects.keys()))
                                projects_per_user[current_user] = projects
                                data["projects_per_user"] = projects_per_user
                                save_data(data)
                                log_action(current_user,"delete_project",name)
                                st.success(f"Project '{name}' deleted.")
                                st.session_state["confirm_delete_project"] = None
                                safe_rerun()
                        else:
                            st.error("Name mismatch")
                    if cancel_delete:
                        st.session_state["confirm_delete_project"] = None
                        safe_rerun()

        st.markdown("---")

        # ------------------------
        # Participant Management
        # ------------------------
        current = st.session_state["current_project"]
        st.header(f"👥 Participants — {current}")
        proj_block = projects.get(current, _default_project_block())
        project_data = proj_block.get("participants", [])

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
                photo = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                submitted = st.form_submit_button("Add Participant")
                if submitted:
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
                        "photo": photo_to_b64(photo) if photo else None
                    }
                    project_data.append(entry)
                    projects[current]["participants"] = project_data
                    projects_per_user[current_user] = projects
                    data["projects_per_user"] = projects_per_user
                    save_data(data)
                    st.success("Participant added!")
                    log_action(current_user,"add_participant",name)
                    safe_rerun()

        if not project_data:
            st.info("No participants yet.")
        else:
            for idx, p in enumerate(project_data):
                with st.container():
                    cols = st.columns([1,2,2,1])
                    if p.get("photo"):
                        try:
                            img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                            cols[0].image(img,width=100)
                        except Exception:
                            cols[0].write("Invalid Photo")
                    else:
                        cols[0].write("No Photo")
                    cols[1].markdown(
                        f"**{p.get('name','Unnamed')}** (#{p.get('number',idx+1)})  \n"
                        f"Role: {p.get('role','')} | Age: {p.get('age','')}  \n"
                        f"Agency: {p.get('agency','')}  \n"
                        f"Height: {p.get('height','')} | Waist: {p.get('waist','')} | Dress/Suit: {p.get('dress_suit','')}  \n"
                        f"Availability: {p.get('availability','')}"
                    )
                    e1,e2 = cols[2].columns([1,1])
                    if e1.button("Edit", key=f"edit_{idx}"):
                        st.session_state["edit_participant_idx"] = idx
                        safe_rerun()
                    if e2.button("Delete", key=f"del_{idx}"):
                        project_data.pop(idx)
                        projects[current]["participants"] = project_data
                        projects_per_user[current_user] = projects
                        data["projects_per_user"] = projects_per_user
                        save_data(data)
                        st.warning("Participant deleted")
                        log_action(current_user,"delete_participant",p.get("name",""))
                        safe_rerun()

        # Inline participant edit
        edit_idx = st.session_state.get("edit_participant_idx")
        if edit_idx is not None and 0 <= edit_idx < len(project_data):
            p = project_data[edit
