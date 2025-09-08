import streamlit as st
import json, os, hashlib, io
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image
import base64

# -----------------------
# Utility Functions
# -----------------------

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def load_users():
    if os.path.exists("users.json"):
        with open("users.json", "r") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open("users.json", "w") as f:
        json.dump(users, f, indent=2)

def load_projects():
    if os.path.exists("projects.json"):
        with open("projects.json", "r") as f:
            return json.load(f)
    return {"Default Project": []}

def save_projects(projects):
    with open("projects.json", "w") as f:
        json.dump(projects, f, indent=2)

def log_action(user, action, detail=""):
    log = st.session_state.get("logs", [])
    log.append({"time": datetime.now().isoformat(), "user": user, "action": action, "detail": detail})
    st.session_state["logs"] = log

def photo_to_b64(photo_file):
    return base64.b64encode(photo_file.read()).decode()

def b64_to_photo(b64_string):
    return base64.b64decode(b64_string)

def safe_rerun():
    st.experimental_set_query_params(dummy=str(datetime.now()))
    st.rerun()

# -----------------------
# App Setup
# -----------------------

st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")
st.title("🎬 Sacha's Casting Manager")

users = load_users()
projects = load_projects()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.page = "login"
    st.session_state.logs = []

# -----------------------
# Login / Signup Page
# -----------------------
if not st.session_state.logged_in:
    st.header("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username == "admin" and password == "supersecret":
            st.session_state.logged_in = True
            st.session_state.current_user = "admin"
            users["admin"] = {
                "password": hash_password(password),
                "role": "Admin",
                "last_login": datetime.now().isoformat(),
                "projects_accessed": []
            }
            save_users(users)
            st.success("Logged in as Admin")
            safe_rerun()

        elif username in users and users[username]["password"] == hash_password(password):
            st.session_state.logged_in = True
            st.session_state.current_user = username
            users[username]["last_login"] = datetime.now().isoformat()
            save_users(users)
            log_action(username, "login")
            safe_rerun()
        else:
            st.error("Invalid username or password")

    st.markdown("---")
    st.subheader("Sign Up")
    new_user = st.text_input("New Username")
    new_pass = st.text_input("New Password", type="password")
    role = st.selectbox("Role", ["Casting Director", "Assistant"])

    if st.button("Sign Up"):
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
            st.success("Account created! Please login.")

else:
    current_user = st.session_state.current_user
    st.sidebar.success(f"Logged in as {current_user}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.current_user = None
        safe_rerun()

    if current_user == "admin" or users.get(current_user, {}).get("role") == "Admin":
        st.sidebar.subheader("Admin Dashboard")
        st.sidebar.write("Manage accounts and view logs")
        if st.sidebar.checkbox("Show Admin Dashboard"):
            st.header("👑 Admin Dashboard")
            st.subheader("All Users")
            for uname, info in list(users.items()):
                if uname == "admin":
                    st.markdown(f"**{uname}** (built-in Admin)")
                    continue

                col1, col2, col3, col4, col5, col6 = st.columns([2,2,3,3,1,1])
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
                if col6.button("✏️", key=f"edituser_{uname}"):
                    st.session_state["editing_user"] = uname
                    safe_rerun()

            if "editing_user" in st.session_state:
                edit_name = st.session_state["editing_user"]
                info = users.get(edit_name, {})
                st.markdown(f"### Edit User: {edit_name}")
                new_role = st.selectbox("Role", ["Admin", "Casting Director", "Assistant"],
                                        index=["Admin","Casting Director","Assistant"].index(info.get("role","Assistant")))
                new_pass = st.text_input("New Password (leave blank to keep)", type="password")
                if st.button("Save Changes"):
                    users[edit_name]["role"] = new_role
                    if new_pass:
                        users[edit_name]["password"] = hash_password(new_pass)
                    save_users(users)
                    st.success(f"Updated {edit_name}")
                    log_action(current_user, "edit_user", edit_name)
                    del st.session_state["editing_user"]
                    safe_rerun()
                if st.button("Cancel Edit"):
                    del st.session_state["editing_user"]
                    safe_rerun()

    st.sidebar.header("Projects")
    current = st.sidebar.selectbox("Select Project", list(projects.keys()))
    project_data = projects[current]
    users[current_user]["projects_accessed"].append(current)
    save_users(users)

    new_project = st.sidebar.text_input("New Project Name")
    if st.sidebar.button("Create Project"):
        if new_project and new_project not in projects:
            projects[new_project] = []
            save_projects(projects)
            st.sidebar.success("Project created!")
            log_action(current_user, "create_project", new_project)
            safe_rerun()

    st.header(f"Participants for {current}")

    with st.expander("➕ Add New Participant"):
        with st.form("add_participant"):
            number = st.text_input("Number")
            name = st.text_input("Name")
            role = st.text_input("Role")
            age = st.text_input("Age")
            agency = st.text_input("Agency")
            height = st.text_input("Height")
            waist = st.text_input("Waist")
            dress_suit = st.text_input("Dress/Suit")
            availability = st.date_input("Next Available")
            photo = st.file_uploader("Photo", type=["jpg","jpeg","png"])

            submitted = st.form_submit_button("Add Participant")
            if submitted:
                photo_b64 = photo_to_b64(photo) if photo else None
                participant = {
                    "number": number, "name": name, "role": role, "age": age, "agency": agency,
                    "height": height, "waist": waist, "dress_suit": dress_suit,
                    "availability": str(availability), "photo": photo_b64
                }
                project_data.append(participant)
                save_projects(projects)
                log_action(current_user, "add_participant", name)
                st.success("Participant added!")
                safe_rerun()

    for i, p in enumerate(project_data):
        with st.container():
            cols = st.columns([1,3,2,2,2,2,2,2,1,1])
            if p.get("photo"):
                img = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                cols[0].image(img, width=70)
            else:
                cols[0].write("No Photo")

            cols[1].write(p.get("name", ""))
            cols[2].write(p.get("role", ""))
            cols[3].write(p.get("age", ""))
            cols[4].write(p.get("agency", ""))
            cols[5].write(p.get("height", ""))
            cols[6].write(p.get("waist", ""))
            cols[7].write(p.get("dress_suit", ""))
            cols[8].write(p.get("availability", ""))
            if cols[9].button("✏️", key=f"edit_{i}"):
                st.session_state["editing"] = i
                safe_rerun()

        if st.session_state.get("editing") == i:
            with st.form(f"edit_{i}_form"):
                name = st.text_input("Name", p["name"])
                role = st.text_input("Role", p["role"])
                age = st.text_input("Age", p["age"])
                agency = st.text_input("Agency", p["agency"])
                height = st.text_input("Height", p["height"])
                waist = st.text_input("Waist", p["waist"])
                dress_suit = st.text_input("Dress/Suit", p["dress_suit"])
                availability = st.date_input("Next Available", datetime.strptime(p["availability"], "%Y-%m-%d").date())
                photo = st.file_uploader("Photo", type=["jpg","jpeg","png"], key=f"edit_photo_{i}")

                save = st.form_submit_button("Save")
                cancel = st.form_submit_button("Cancel")
                if save:
                    p.update({"name": name, "role": role, "age": age, "agency": agency,
                               "height": height, "waist": waist, "dress_suit": dress_suit,
                               "availability": str(availability)})
                    if photo:
                        p["photo"] = photo_to_b64(photo)
                    save_projects(projects)
                    log_action(current_user, "edit_participant", name)
                    st.session_state["editing"] = None
                    safe_rerun()
                elif cancel:
                    st.session_state["editing"] = None
                    safe_rerun()

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
