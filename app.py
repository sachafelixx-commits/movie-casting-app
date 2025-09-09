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

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

def photo_to_b64(file):
    return base64.b64encode(file.read()).decode("utf-8")

def b64_to_photo(b64_string):
    return base64.b64decode(b64_string)

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
                users = load_users()
                users["admin"] = {
                    "password": hash_password(password),
                    "role": "Admin",
                    "last_login": datetime.now().isoformat(),
                    "projects": {}
                }
                save_users(users)
                st.success("Logged in as Admin ✅")
                safe_rerun()

            if username in users and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                save_users(users)
                st.success(f"Welcome back {username}!")
                safe_rerun()
            else:
                st.error("Invalid credentials")

    else:  # Sign Up
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
    user_data = users[current_user]
    role = user_data.get("role", "Casting Director")
    projects = user_data.get("projects", {})

    # Ensure at least default project exists
    if DEFAULT_PROJECT_NAME not in projects:
        projects[DEFAULT_PROJECT_NAME] = _default_project_block()

    # Sidebar
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{current_user}**")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        save_users(users)
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
        st.caption(f"Submitting to project: **{active}**")

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
                projects[active]["participants"].append(entry)
                users[current_user]["projects"] = projects
                save_users(users)
                st.success("✅ Thanks for checking in!")
                safe_rerun()

    # ===== Casting Manager Mode =====
    else:
        st.title("🎬 Sacha's Casting Manager")
        st.header("📁 Project Manager")

        # Create & Edit Projects
        with st.expander("➕ Create New Project"):
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
                        projects[p_name] = {"description": p_desc or "", "created_at": datetime.now().isoformat(), "participants": []}
                        st.session_state["current_project"] = p_name
                        users[current_user]["projects"] = projects
                        save_users(users)
                        st.success(f"Project '{p_name}' created.")
                        safe_rerun()

        # Display Projects
        for pname, pblock in projects.items():
            cols = st.columns([3, 4, 2, 2, 4])
            is_active = (pname == active)
            cols[0].markdown(f"{'🟢 ' if is_active else ''}**{pname}**")
            cols[1].markdown(pblock.get("description","—"))
            cols[2].markdown(pblock.get("created_at","—").split('T')[0])
            cols[3].markdown(str(len(pblock.get("participants",[]))))

            a1,a2,a3 = cols[4].columns(3)
            if a1.button("Set Active", key=f"setactive_{pname}"):
                st.session_state["current_project"] = pname
                safe_rerun()
            if a2.button("Delete", key=f"delproj_{pname}"):
                if len(projects) <= 1:
                    st.error("You must keep at least one project.")
                else:
                    projects.pop(pname, None)
                    if st.session_state["current_project"] == pname:
                        st.session_state["current_project"] = next(iter(projects.keys()))
                    users[current_user]["projects"] = projects
                    save_users(users)
                    st.warning(f"Project '{pname}' deleted.")
                    safe_rerun()

        st.markdown("---")

        # Participant Management
        st.header(f"👥 Participants — {active}")
        project_data = projects[active].get("participants", [])

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
                    projects[active]["participants"] = project_data
                    users[current_user]["projects"] = projects
                    save_users(users)
                    st.success("Participant added!")
                    safe_rerun()

        # Display Participants
        for idx, p in enumerate(project_data):
            cols = st.columns([1,2,1])
            if p.get("photo"):
                try:
                    img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                    cols[0].image(img, width=100)
                except:
                    cols[0].write("Invalid Photo")
            else:
                cols[0].write("No Photo")
            cols[1].markdown(f"**{p.get('name','Unnamed')}** (#{p.get('number','')})  
Role: {p.get('role','')} | Age: {p.get('age','')}  
Agency: {p.get('agency','')}  
Height: {p.get('height','')} | Waist: {p.get('waist','')} | Dress/Suit: {p.get('dress_suit','')}  
Availability: {p.get('availability','')}")
            if cols[2].button("Delete", key=f"del_{idx}"):
                project_data.pop(idx)
                projects[active]["participants"] = project_data
                users[current_user]["projects"] = projects
                save_users(users)
                st.warning("Participant deleted")
                safe_rerun()

        # Export Participants to Word
        st.subheader("📄 Export Participants (Word)")
        if st.button("Download Word File of Current Project"):
            if project_data:
                doc = Document()
                doc.add_heading(f"Participants - {active}",0)
                for p in project_data:
                    table = doc.add_table(rows=1,cols=2)
                    row_cells = table.rows[0].cells
                    if p.get("photo"):
                        try:
                            image_stream = io.BytesIO(b64_to_photo(p["photo"]))
                            run = row_cells[0].paragraphs[0].add_run()
                            run.add_picture(image_stream, width=Inches(1.5))
                        except:
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
                        f"Next Available: {p.get('availability','')}")
                    row_cells[1].text = info_text
                    doc.add_paragraph("\n")
                word_stream = io.BytesIO()
                doc.save(word_stream)
                word_stream.seek(0)
                st.download_button("Click to download Word file", word_stream, file_name=f"{active}_participants.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
