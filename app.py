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
# Constants
# ========================
USERS_FILE = "users.json"
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
for key in ["page", "logged_in", "current_user", "current_project", "participant_mode",
            "confirm_delete_project", "editing_project", "editing_participant"]:
    if key not in st.session_state:
        st.session_state[key] = False if key == "logged_in" else None

# ========================
# Auth Screens
# ========================
users = load_users()

if not st.session_state["logged_in"]:
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
                users = load_users()
                users["admin"] = users.get("admin", {})
                users["admin"]["password"] = hash_password(password)
                users["admin"]["role"] = "Admin"
                users["admin"]["last_login"] = datetime.now().isoformat()
                users["admin"]["projects"] = users["admin"].get("projects", {DEFAULT_PROJECT_NAME: _default_project_block()})
                save_users(users)
                log_action("admin", "login")
                st.success("Logged in as Admin ✅")
                safe_rerun()

            users = load_users()
            if username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                if "projects" not in users[username]:
                    users[username]["projects"] = {DEFAULT_PROJECT_NAME: _default_project_block()}
                save_users(users)
                log_action(username, "login")
                st.success(f"Welcome back {username}!")
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
                users[new_user] = {
                    "password": hash_password(new_pass),
                    "role": role,
                    "last_login": datetime.now().isoformat(),
                    "projects": {DEFAULT_PROJECT_NAME: _default_project_block()}
                }
                save_users(users)
                st.success("Account created! Please log in.")
                safe_rerun()

# ========================
# Main App
# ========================
else:
    current_user = st.session_state["current_user"]
    users = load_users()  # always load fresh
    user_data = users[current_user]
    projects = user_data.get("projects", {})

    if not projects:
        projects[DEFAULT_PROJECT_NAME] = _default_project_block()
        user_data["projects"] = projects
        save_users(users)

    # ---------------- Sidebar ----------------
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")
    role = user_data.get("role", "Casting Director")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        safe_rerun()

    st.sidebar.subheader("Modes")
    st.session_state["participant_mode"] = st.sidebar.toggle(
        "Enable Participant Mode (Kiosk)",
        value=st.session_state.get("participant_mode", False)
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    active = st.session_state.get("current_project", DEFAULT_PROJECT_NAME)
    st.sidebar.write(f"**{active}**")

    # ================= Participant Mode =================
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
                projects = user_data.get("projects", {})
                proj_block = projects.get(active, _default_project_block())
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
                proj_block["participants"] = participants
                projects[active] = proj_block
                user_data["projects"] = projects
                users[current_user] = user_data
                save_users(users)
                st.success("✅ Thanks for checking in! Next participant may proceed.")
                log_action(current_user, "participant_checkin", name)
                safe_rerun()

    # ================= Casting Manager Mode =================
    else:
        st.title("🎬 Sacha's Casting Manager")
        # ================= Project Manager =================
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

        with st.expander("➕ Create New Project", expanded=False):
            with st.form("new_project_form"):
                p_name = st.text_input("Project Name")
                p_desc = st.text_area("Description", height=80)
                create_btn = st.form_submit_button("Create Project")
                if create_btn:
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
                        save_users(users)
                        log_action(current_user, "create_project", p_name)
                        st.success(f"Project '{p_name}' created.")
                        st.session_state["current_project"] = p_name
                        safe_rerun()

        def proj_meta_tuple(name, block):
            count = len(block.get("participants", []))
            created = block.get("created_at", datetime.now().isoformat())
            return name, block.get("description", ""), created, count

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

        hdr = st.columns([3, 4, 2, 2, 4])
        hdr[0].markdown("**Project**")
        hdr[1].markdown("**Description**")
        hdr[2].markdown("**Created**")
        hdr[3].markdown("**Participants**")
        hdr[4].markdown("**Actions**")

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

            # Inline edit and deletion remain same but always use current_user -> projects
            # ... [rest of Casting Manager & Participant Management unchanged] ...

# ================= Admin Dashboard =================
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
                items.append((u, info.get("role",""), info.get("last_login",""), ", ".join(info.get("projects",[]))))

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
                    if uname == "admin" and role_sel != "Admin":
                        st.error("Built-in admin must remain Admin.")
                    else:
                        admin_users[uname]["role"] = role_sel
                        save_users(admin_users)
                        log_action(current_user, "change_role", f"{uname} -> {role_sel}")
                        st.success(f"Role updated for {uname}.")
                        safe_rerun()

                if a2.button("Delete", key=f"deluser_{uname}"):
                    if uname == "admin":
                        st.error("Cannot delete the built-in admin.")
                    else:
                        admin_users.pop(uname, None)
                        save_users(admin_users)
                        log_action(current_user, "delete_user", uname)
                        st.warning(f"User {uname} deleted.")
                        safe_rerun()
