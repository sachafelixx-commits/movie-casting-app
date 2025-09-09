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
            try:
                return json.load(f)
            except Exception:
                return default
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
    if not file:
        return None
    try:
        return base64.b64encode(file.read()).decode("utf-8")
    except Exception:
        return None

def b64_to_photo(b64_string):
    if not b64_string:
        return None
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
    st.session_state["current_project"] = None
if "participant_mode" not in st.session_state:
    st.session_state["participant_mode"] = False
if "confirm_delete_project" not in st.session_state:
    st.session_state["confirm_delete_project"] = None
if "editing_project" not in st.session_state:
    st.session_state["editing_project"] = None
if "editing_participant" not in st.session_state:
    st.session_state["editing_participant"] = None

# initial load of users (not authoritative — always reload before writes)
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
                save_users(users)
                log_action("admin", "login")
                st.success("Logged in as Admin ✅")
                safe_rerun()

            users = load_users()  # reload
            if username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                # ensure at least one project exists for this user
                if "projects" not in users[username] or not isinstance(users[username]["projects"], dict):
                    users[username]["projects"] = {DEFAULT_PROJECT_NAME: _default_project_block()}
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
                save_users(users)
                st.success("Account created! Please log in.")
                safe_rerun()

# ========================
# Main App (after login)
# ========================
else:
    # ensure we always work off a fresh users dict for reads
    users = load_users()
    current_user = st.session_state["current_user"]

    # guard: if current_user somehow missing from users, log out
    if current_user not in users:
        st.error("User not found. Please log in again.")
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        safe_rerun()

    user_data = users[current_user]
    # ensure projects key exists and is a dict
    projects = user_data.get("projects", {})
    if not isinstance(projects, dict) or not projects:
        projects = {DEFAULT_PROJECT_NAME: _default_project_block()}
        user_data["projects"] = projects
        users[current_user] = user_data
        save_users(users)

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
    # keep the same toggle call as original (if your streamlit version doesn't support toggle, replace with checkbox)
    try:
        st.session_state["participant_mode"] = st.sidebar.toggle(
            "Enable Participant Mode (Kiosk)",
            value=st.session_state.get("participant_mode", False)
        )
    except Exception:
        # fallback for streamlit versions without toggle
        st.session_state["participant_mode"] = st.sidebar.checkbox(
            "Enable Participant Mode (Kiosk)",
            value=st.session_state.get("participant_mode", False)
        )

    # Active Project Display in Sidebar
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Project")
    # default to existing current_project if it's in this user's projects, otherwise set to first project
    if st.session_state.get("current_project") not in projects:
        st.session_state["current_project"] = next(iter(projects.keys()))
    active = st.session_state.get("current_project", DEFAULT_PROJECT_NAME)
    st.sidebar.write(f"**{active}**")

    # Participant Mode (Kiosk)
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
                # reload users and user_data fresh before write
                users = load_users()
                if current_user not in users:
                    st.error("User not found. Please log in again.")
                    safe_rerun()
                user_data = users[current_user]
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

    # Casting Manager Mode
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
                        save_users(users)
                        log_action(current_user, "create_project", p_name)
                        st.success(f"Project '{p_name}' created.")
                        st.session_state["current_project"] = p_name
                        safe_rerun()

        # Prepare filtered/sorted list
        def proj_meta_tuple(name, block):
            count = len(block.get("participants", []))
            created = block.get("created_at", datetime.now().isoformat())
            return name, block.get("description", ""), created, count

        # Always work off the up-to-date projects dict for rendering
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
                            projects[new_name] = block
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = new_name
                            user_data["projects"] = projects
                            users[current_user] = user_data
                            save_users(users)
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
                            projects.pop(name, None)
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = next(iter(projects.keys()))
                            user_data["projects"] = projects
                            users[current_user] = user_data
                            save_users(users)
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
                    user_data["projects"] = projects
                    users[current_user] = user_data
                    save_users(users)
                    st.success("Participant added!")
                    log_action(current_user, "add_participant", name)
                    safe_rerun()

        if not project_data:
            st.info("No participants yet.")
        else:
            for idx, p in enumerate(project_data):
                with st.container():
                    cols = st.columns([1, 2, 1, 2])
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
                                    "photo": photo_to_b64(ephoto) if ephoto else p.get("photo")
                                })
                                projects[current]["participants"] = project_data
                                user_data["projects"] = projects
                                users[current_user] = user_data
                                save_users(users)
                                st.success("Participant updated!")
                                log_action(current_user, "edit_participant", ename)
                                safe_rerun()
                            if cancel_edit:
                                safe_rerun()

                    # Delete participant
                    if d_btn.button("Delete", key=f"del_{idx}"):
                        project_data.pop(idx)
                        projects[current]["participants"] = project_data
                        user_data["projects"] = projects
                        users[current_user] = user_data
                        save_users(users)
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
                        save_users(admin_users)
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
                        admin_users.pop(uname, None)
                        save_users(admin_users)
                        log_action(current_user, "delete_user", uname)
                        st.warning(f"User {uname} deleted.")
                        safe_rerun()
