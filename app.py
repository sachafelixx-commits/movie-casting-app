import streamlit as st
import json, os, io, base64
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import hashlib

# ========================
# Page Config (set once)
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
    # Backward-compatible loader: supports old {"projects": {name: [participants...]}} schema
    raw = load_json(DATA_FILE, {"projects": {}})
    projects = raw.get("projects", {})

    # If empty, create default project
    if not projects:
        projects[DEFAULT_PROJECT_NAME] = _default_project_block()
        return {"projects": projects}

    # Normalize any legacy list-only projects to the new dict format
    changed = False
    for name, block in list(projects.items()):
        if isinstance(block, list):
            projects[name] = {
                "description": "",
                "created_at": datetime.now().isoformat(),
                "participants": block,
            }
            changed = True
        elif isinstance(block, dict) and "participants" not in block:
            projects[name]["participants"] = []
            changed = True
    data = {"projects": projects}
    if changed:
        save_data(data)
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

users = load_users()
data = load_data()

# Ensure active project exists
projects = data["projects"]
if st.session_state["current_project"] not in projects:
    projects[st.session_state["current_project"]] = _default_project_block()
    save_data(data)

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

            users = load_users()  # always reload
            if username in users and isinstance(users[username], dict) and users[username]["password"] == hash_password(password):
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
        users = load_users()  # reload fresh
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        role = st.selectbox("Role", ["Casting Director", "Assistant"])  # Admin only via special login
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

    # Sidebar
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")

    # Reload users to get latest roles after any admin edits
    users = load_users()
    role = users.get(current_user, {}).get("role", "Casting Director")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        safe_rerun()

    # Modes
    st.sidebar.subheader("Modes")
    st.session_state["participant_mode"] = st.sidebar.toggle(
        "Enable Participant Mode (Kiosk)",
        value=st.session_state["participant_mode"]
    )

    # Active Project Display in Sidebar
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    active = st.session_state["current_project"]
    st.sidebar.write(f"**{active}**")

    # ===== Participant Mode =====
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
                # Ensure project block exists
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
                save_data(data)
                st.success("✅ Thanks for checking in! Next participant may proceed.")
                log_action(current_user, "participant_checkin", name)
                safe_rerun()

    # ===== Casting Manager Mode =====
    else:
        st.title("🎬 Sacha's Casting Manager")

        # ------------------------
        # Project Manager (facelift + search)
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
                        save_data(data)
                        log_action(current_user, "create_project", p_name)
                        st.success(f"Project '{p_name}' created.")
                        st.session_state["current_project"] = p_name
                        safe_rerun()

        # Prepare filtered/sorted list
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
                save_data(data)
                st.success(f"Active project set to '{name}'.")
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
                        if not new_name:
                            st.error("Name cannot be empty.")
                        elif new_name != name and new_name in projects:
                            st.error("Another project already has this name.")
                        else:
                            block = projects.pop(name)
                            block["description"] = new_desc
                            projects[new_name] = block
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = new_name
                            save_data(data)
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
                        if len(projects) <= 1:
                            st.error("You must keep at least one project.")
                        else:
                            projects.pop(name, None)
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = next(iter(projects.keys()))
                            save_data(data)
                            log_action(current_user, "delete_project", name)
                            st.success(f"Project '{name}' deleted.")
                            st.session_state["confirm_delete_project"] = None
                            safe_rerun()
                    else:
                        st.error("Project name mismatch. Not deleted.")
                if cancel_delete:
                    st.session_state["confirm_delete_project"] = None
                    safe_rerun()

        st.markdown("---")

        # ------------------------
        # Admin Dashboard (facelift + search)
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
            uhdr[3].markdown("**Projects Accessed**")
            uhdr[4].markdown("**Actions**")

            # Build items
            def user_item(u, info):
                return (
                    u,
                    info.get("role", ""),
                    info.get("last_login", ""),
                    ", ".join(info.get("projects_accessed", []))
                )

            items = [user_item(u, i) for u, i in admin_users.items() if isinstance(i, dict)]
            if uquery:
                q = uquery.lower().strip()
                items = [x for x in items if q in x[0].lower() or q in (x[1] or "").lower()]
            if urole_filter != "All":
                items = [x for x in items if x[1] == urole_filter]

            for uname, urole, last, projlist in items:
                cols = st.columns([3, 2, 3, 3, 4])
                cols[0].markdown(f"**{uname}**")
                # role editor
                role_sel = cols[1].selectbox(
                    "role_sel_" + uname,
                    ["Admin", "Casting Director", "Assistant"],
                    index=["Admin", "Casting Director", "Assistant"].index(urole) if urole in ["Admin", "Casting Director", "Assistant"] else 1,
                    key=f"role_sel_{uname}"
                )
                cols[2].markdown(last or "—")
                cols[3].markdown(projlist or "—")

                a1, a2 = cols[4].columns([1, 1])
                if a1.button("Save Role", key=f"saverole_{uname}"):
                    if uname == "admin" and role_sel != "Admin":
                        st.error("The built-in admin must remain Admin.")
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

            st.subheader("Activity Logs")
            st.json(load_logs())

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
                photo = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png"])
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
                    save_data(data)
                    st.success("Participant added!")
                    log_action(current_user, "add_participant", name)
                    safe_rerun()

        if not project_data:
            st.info("No participants yet.")
        else:
            for idx, p in enumerate(project_data):
                with st.container():
                    cols = st.columns([1, 2, 1])
                    if p.get("photo"):
                        try:
                            img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
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
                    if cols[2].button("Delete", key=f"del_{idx}"):
                        project_data.pop(idx)
                        projects[current]["participants"] = project_data
                        save_data(data)
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

                    if p.get("photo"):
                        try:
                            image_stream = io.BytesIO(b64_to_photo(p["photo"]))
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
