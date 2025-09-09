
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

def log_action(user, action, details=""):
    logs = load_json("logs.json", [])
    logs.append({
        "timestamp": datetime.now().isoformat(),
        "user": user,
        "action": action,
        "details": details
    })
    save_json("logs.json", logs)

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
            if username == "admin" and password == "supersecret":
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = "admin"
                if "admin" not in users:
                    users["admin"] = {
                        "password": hash_password(password),
                        "role": "Admin",
                        "projects": {DEFAULT_PROJECT_NAME: _default_project_block()},
                        "last_login": datetime.now().isoformat()
                    }
                    save_users(users)
                log_action("admin", "login")
                st.success("Logged in as Admin ✅")
                safe_rerun()

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

    else:  # Sign Up
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
                    "projects": {DEFAULT_PROJECT_NAME: _default_project_block()},
                    "last_login": datetime.now().isoformat()
                }
                save_users(users)
                st.success("Account created! Please log in.")
                safe_rerun()

# ========================
# Main App
# ========================
else:
    current_user = st.session_state["current_user"]
    user_data = users[current_user]
    role = user_data.get("role", "Casting Director")
    projects = user_data.get("projects", {})

    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")

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
                users[current_user]["projects"] = projects
                save_users(users)
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
                        users[current_user]["projects"] = projects
                        save_users(users)
                        log_action(current_user, "create_project", p_name)
                        st.success(f"Project '{p_name}' created.")
                        st.session_state["current_project"] = p_name
                        safe_rerun()

        # Render projects
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
                            users[current_user]["projects"] = projects
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = new_name
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
                        if len(projects) <= 1:
                            st.error("You must keep at least one project.")
                        else:
                            projects.pop(name, None)
                            users[current_user]["projects"] = projects
                            if st.session_state["current_project"] == name:
                                st.session_state["current_project"] = next(iter(projects.keys()))
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
        st.markdown("---")

        # ------------------------
        # Participant Management
        # ------------------------
        current = st.session_state["current_project"]
        proj_block = projects.get(current, _default_project_block())
        project_data = proj_block.get("participants", [])

        st.header(f"👥 Participants — {current}")

        # Add New Participant
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
                    users[current_user]["projects"] = projects
                    save_users(users)
                    st.success("Participant added!")
                    log_action(current_user, "add_participant", name)
                    safe_rerun()

        if not project_data:
            st.info("No participants yet.")
        else:
            for idx, p in enumerate(project_data):
                with st.container():
                    cols = st.columns([1, 2, 2])
                    # Display photo
                    if p.get("photo"):
                        try:
                            img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                            cols[0].image(img, width=100)
                        except Exception:
                            cols[0].write("Invalid Photo")
                    else:
                        cols[0].write("No Photo")

                    # Display participant info
                    cols[1].markdown(
                        f"**{p.get('name','Unnamed')}** (#{p.get('number','')})  \n"
                        f"Role: {p.get('role','')} | Age: {p.get('age','')}  \n"
                        f"Agency: {p.get('agency','')}  \n"
                        f"Height: {p.get('height','')} | Waist: {p.get('waist','')} | Dress/Suit: {p.get('dress_suit','')}  \n"
                        f"Availability: {p.get('availability','')}"
                    )

                    # Edit / Delete buttons
                    edit_btn, delete_btn = cols[2].columns(2)
                    if edit_btn.button("Edit", key=f"edit_{idx}"):
                        with st.form(f"edit_participant_form_{idx}"):
                            number_e = st.text_input("Number", value=p.get("number",""))
                            name_e = st.text_input("Name", value=p.get("name",""))
                            role_e = st.text_input("Role", value=p.get("role",""))
                            age_e = st.text_input("Age", value=p.get("age",""))
                            agency_e = st.text_input("Agency", value=p.get("agency",""))
                            height_e = st.text_input("Height", value=p.get("height",""))
                            waist_e = st.text_input("Waist", value=p.get("waist",""))
                            dress_suit_e = st.text_input("Dress/Suit", value=p.get("dress_suit",""))
                            availability_e = st.text_input("Next Availability", value=p.get("availability",""))
                            photo_e = st.file_uploader("Upload Photo", type=["jpg","jpeg","png"])
                            save_edit = st.form_submit_button("Save")
                            cancel_edit = st.form_submit_button("Cancel")

                            if save_edit:
                                p.update({
                                    "number": number_e,
                                    "name": name_e,
                                    "role": role_e,
                                    "age": age_e,
                                    "agency": agency_e,
                                    "height": height_e,
                                    "waist": waist_e,
                                    "dress_suit": dress_suit_e,
                                    "availability": availability_e,
                                    "photo": photo_to_b64(photo_e) if photo_e else p.get("photo")
                                })
                                projects[current]["participants"] = project_data
                                users[current_user]["projects"] = projects
                                save_users(users)
                                st.success("Participant updated!")
                                log_action(current_user, "edit_participant", name_e)
                                safe_rerun()
                            if cancel_edit:
                                safe_rerun()

                    if delete_btn.button("Delete", key=f"del_{idx}"):
                        project_data.pop(idx)
                        projects[current]["participants"] = project_data
                        users[current_user]["projects"] = projects
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
