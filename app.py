import streamlit as st
import json, os, io, base64
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import hashlib

# ------------------------
# Utility Functions
# ------------------------

USERS_FILE = "users.json"
DATA_FILE = "casting_data.json"
LOG_FILE = "logs.json"

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

def load_data():
    return load_json(DATA_FILE, {"projects": {}})

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
        pass

# ------------------------
# Session State Init
# ------------------------
if "page" not in st.session_state:
    st.session_state["page"] = "login"
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

users = load_users()
data = load_data()

# ------------------------
# Authentication
# ------------------------
if not st.session_state["logged_in"]:
    st.title("🎬 Sacha's Casting Manager")

    choice = st.radio("Choose an option", ["Login", "Sign Up"])

    if choice == "Login":
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")

        if login_btn:
            # --- Hardcoded admin backdoor ---
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
                st.session_state["page"] = "main"
                st.success("Logged in as Admin ✅")
                safe_rerun()

            elif username in users and isinstance(users[username], dict) and users[username]["password"] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["current_user"] = username
                users[username]["last_login"] = datetime.now().isoformat()
                save_users(users)
                st.session_state["page"] = "main"
                st.success(f"Welcome back {username}!")
                log_action(username, "login")
                safe_rerun()
            else:
                st.error("Invalid credentials")

    else:  # Sign Up
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        role = st.selectbox("Role", ["Casting Director", "Assistant"])
        signup_btn = st.button("Sign Up")

        if signup_btn:
            if new_user in users:
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
else:
    # ------------------------
    # Main App
    # ------------------------
    st.sidebar.title("Menu")
    st.sidebar.write(f"Logged in as: **{st.session_state['current_user']}**")
    current_user = st.session_state["current_user"]
    role = users.get(current_user, {}).get("role", "Casting Director")

    if st.sidebar.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["current_user"] = None
        st.session_state["page"] = "login"
        safe_rerun()

    # Projects
    st.sidebar.subheader("Projects")
    all_projects = list(data["projects"].keys())
    if all_projects:
        selected_project = st.sidebar.selectbox("Select Project", all_projects, index=0)
        if selected_project:
            st.session_state["current_project"] = selected_project
            if current_user in users and isinstance(users[current_user], dict):
                if selected_project not in users[current_user]["projects_accessed"]:
                    users[current_user]["projects_accessed"].append(selected_project)
                    save_users(users)

    new_proj = st.sidebar.text_input("Create New Project")
    if st.sidebar.button("Add Project") and new_proj:
        if new_proj not in data["projects"]:
            data["projects"][new_proj] = []
            save_data(data)
            st.session_state["current_project"] = new_proj
            st.success(f"Project '{new_proj}' added!")
            log_action(current_user, "create_project", new_proj)
            safe_rerun()
        else:
            st.error("Project already exists")

    # ------------------------
    # Admin Dashboard
    # ------------------------
    if role == "Admin":
        st.sidebar.subheader("🔐 Admin Dashboard")
        if st.sidebar.checkbox("Show Admin Panel"):
            st.header("Admin Dashboard")

            st.subheader("All Users")
            if users:
                for uname, info in list(users.items()):
                    if not isinstance(info, dict):
                        continue  # skip invalid entries

                    if uname == "admin":
                        st.markdown(f"**{uname}** (built-in Admin)")
                        continue

                    col1, col2, col3, col4, col5 = st.columns([2,2,3,3,1])
                    col1.write(uname)
                    col2.write(info.get("role", ""))
                    col3.write(info.get("last_login", ""))
                    col4.write(", ".join(info.get("projects_accessed", [])))
                    if col5.button("❌", key=f"deluser_{uname}"):
                        users.pop(uname)
                        save_users(users)
                        st.warning(f"User {uname} deleted.")
                        log_action(current_user, "delete_user", uname)
                        safe_rerun()
            else:
                st.info("No users yet.")

            st.subheader("Activity Logs")
            st.json(load_logs())

    # ------------------------
    # Participant Management
    # ------------------------
    current = st.session_state["current_project"]
    st.title(f"🎥 Project: {current}")
    project_data = data["projects"].get(current, [])

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
                data["projects"][current] = project_data
                save_data(data)
                st.success("Participant added!")
                log_action(current_user, "add_participant", name)
                safe_rerun()

    st.subheader("👥 Participants")
    if not project_data:
        st.info("No participants yet.")
    else:
        for idx, p in enumerate(project_data):
            with st.container():
                cols = st.columns([1, 2, 1])
                if p["photo"]:
                    img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                    cols[0].image(img, width=100)
                else:
                    cols[0].write("No Photo")
                cols[1].markdown(
                    f"**{p['name']}** (#{p.get('number','')})  \n"
                    f"Role: {p['role']} | Age: {p['age']}  \n"
                    f"Agency: {p['agency']}  \n"
                    f"Height: {p['height']} | Waist: {p['waist']} | Dress/Suit: {p['dress_suit']}  \n"
                    f"Availability: {p['availability']}"
                )
                if cols[2].button("Delete", key=f"del_{idx}"):
                    project_data.pop(idx)
                    data["projects"][current] = project_data
                    save_data(data)
                    st.warning("Participant deleted")
                    log_action(current_user, "delete_participant", p["name"])
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

                if p["photo"]:
                    image_stream = io.BytesIO(b64_to_photo(p["photo"]))
                    paragraph = row_cells[0].paragraphs[0]
                    run = paragraph.add_run()
                    run.add_picture(image_stream, width=Inches(1.5))
                else:
                    row_cells[0].text = "No Photo"

                info_text = (
                    f"Number: {p.get('number','')}\n"
                    f"Name: {p['name']}\n"
                    f"Role: {p['role']}\n"
                    f"Age: {p['age']}\n"
                    f"Agency: {p['agency']}\n"
                    f"Height: {p['height']}\n"
                    f"Waist: {p['waist']}\n"
                    f"Dress/Suit: {p['dress_suit']}\n"
                    f"Next Available: {p['availability']}"
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
