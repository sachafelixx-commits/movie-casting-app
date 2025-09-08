import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import json
import base64
import os
from datetime import datetime

# ------------------------
# Helpers for persistence
# ------------------------
USERS_FILE = "users.json"
DATA_FILE = "casting_data.json"
LOG_FILE = "logs.json"

# Hash password
ndef hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Save users
ndef save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# Load users
ndef load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}

# Save data
ndef save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(st.session_state["projects"], f)

# Load data
ndef load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"Default Project": []}

# Save logs
ndef log_action(user, action, details=""):
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    logs.append({
        "time": datetime.now().isoformat(),
        "user": user,
        "action": action,
        "details": details
    })
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

# Load logs
ndef load_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return []

# Convert photo bytes to base64 string
def photo_to_b64(photo_bytes):
    return base64.b64encode(photo_bytes).decode("utf-8")

# Convert base64 string back to photo bytes
def b64_to_photo(b64_str):
    return base64.b64decode(b64_str)

# ------------------------
# Page setup
# ------------------------
st.set_page_config(page_title="🎬 Sacha's Casting Manager", layout="wide")

# Custom CSS
st.markdown("""
<style>
.card {
    background-color: #FAFAFA;
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 6px 16px rgba(0,0,0,0.06);
}
.card h3 {
    margin-bottom: 8px;
    font-size: 20px;
    font-weight: 600;
    color: #222;
}
.role-tag {
    color: white;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 13px;
    font-weight: 500;
    display: inline-block;
}
.detail-label {font-weight: 600; color: #555;}
.detail-value {color: #222; font-weight: 400;}
.detail-row {border-top: 1px solid #EEE; padding-top: 6px; margin-top: 6px;}
</style>
""", unsafe_allow_html=True)

# ------------------------
# Load / init session state
# ------------------------
users = load_users()

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "projects" not in st.session_state:
    st.session_state["projects"] = load_data()
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"
if "editing" not in st.session_state:
    st.session_state["editing"] = None
if "page" not in st.session_state:
    st.session_state["page"] = "login"

# ------------------------
# Login & Signup
# ------------------------
def login_page():
    st.title("🎬 Sacha's Casting Manager")
    st.subheader("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        # Special admin login
        if username == "admin" and password == "supersecret":
            st.session_state["logged_in"] = True
            st.session_state["current_user"] = "admin"
            if "admin" not in users:
                users["admin"] = {
                    "password": hash_password(password),
                    "role": "Admin",
                    "last_login": datetime.now().isoformat(),
                    "projects_accessed": []
                }
                save_users(users)
            st.session_state["page"] = "main"
            log_action("admin", "login")
            st.success("Logged in as Admin")
            st.experimental_rerun()
        elif username in users and users[username]["password"] == hash_password(password):
            st.session_state["logged_in"] = True
            st.session_state["current_user"] = username
            users[username]["last_login"] = datetime.now().isoformat()
            save_users(users)
            st.session_state["page"] = "main"
            log_action(username, "login")
            st.experimental_rerun()
        else:
            st.error("Invalid credentials")
    
    st.subheader("Sign Up")
    new_username = st.text_input("New Username")
    new_password = st.text_input("New Password", type="password")
    role = st.selectbox("Role", ["Casting Director", "Assistant"])
    if st.button("Sign Up"):
        if new_username in users:
            st.error("Username already exists")
        else:
            users[new_username] = {
                "password": hash_password(new_password),
                "role": role,
                "last_login": datetime.now().isoformat(),
                "projects_accessed": []
            }
            save_users(users)
            st.success("Account created! Please log in.")

# ------------------------
# Role color generator
# ------------------------
def role_color(role):
    h = hashlib.md5(role.encode()).hexdigest()
    r = int(h[:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"#{r:02X}{g:02X}{b:02X}"

# ------------------------
# Main app
# ------------------------
def main_app():
    current_user = st.session_state["current_user"]
    st.sidebar.write(f"👤 Logged in as: {current_user}")

    # Admin dashboard
    if current_user == "admin" or users.get(current_user, {}).get("role") == "Admin":
        st.sidebar.subheader("⚙️ Admin Dashboard")
        st.sidebar.write("Manage accounts and logs")
        if st.sidebar.checkbox("Show Admin Panel"):
            st.header("Admin Panel")

            # Show all users
            st.subheader("All Users")
            if users:
                for uname, info in list(users.items()):
                    if not isinstance(info, dict):
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
                        st.experimental_rerun()
                    if col6.button("✏️", key=f"edituser_{uname}"):
                        with st.form(f"edit_user_form_{uname}"):
                            new_role = st.selectbox("Role", ["Casting Director", "Assistant", "Admin"],
                                index=["Casting Director", "Assistant", "Admin"].index(info.get("role", "Casting Director")))
                            new_pass = st.text_input("New Password (leave blank to keep)", type="password")
                            save_edit = st.form_submit_button("Save")
                            if save_edit:
                                info["role"] = new_role
                                if new_pass:
                                    info["password"] = hash_password(new_pass)
                                users[uname] = info
                                save_users(users)
                                st.success(f"Updated {uname}")
                                log_action(current_user, "edit_user", uname)
                                st.experimental_rerun()
            else:
                st.info("No users yet.")

            # Show logs
            st.subheader("Activity Logs")
            logs = load_logs()
            for log in reversed(logs[-20:]):
                st.write(f"{log['time']} - {log['user']} - {log['action']} {log['details']}")

    # Sidebar project manager
    st.sidebar.header("📂 Project Manager")
    project_names = list(st.session_state["projects"].keys())
    selected_project = st.sidebar.selectbox(
        "Select Project", project_names, index=project_names.index(st.session_state["current_project"])
    )
    st.session_state["current_project"] = selected_project
    current = st.session_state["current_project"]

    # Add new project
    with st.sidebar.expander("➕ Create Project"):
        new_proj = st.text_input("Project name")
        if st.button("Add Project"):
            if new_proj and new_proj not in st.session_state["projects"]:
                st.session_state["projects"][new_proj] = []
                st.session_state["current_project"] = new_proj
                save_data()
                log_action(current_user, "create_project", new_proj)
                st.success(f"Project '{new_proj}' added!")
                st.experimental_rerun()

    # Participants
    st.markdown(f"<h2 style='color:#1E1E1E;'>Participants in {current}</h2>", unsafe_allow_html=True)
    with st.expander("➕ Add New Participant"):
        with st.form("add_participant_form"):
            number = st.number_input("Participant #", min_value=1, value=len(st.session_state["projects"][current]) + 1)
            name = st.text_input("Name")
            age = st.text_input("Age")
            agency = st.text_input("Agency")
            height = st.text_input("Height")
            waist = st.text_input("Waist")
            dress_suit = st.text_input("Dress/Suit Size")
            role = st.text_input("Role/Status")
            availability = st.text_area("Availability")
            photo = st.file_uploader("Upload Picture", type=["png", "jpg", "jpeg"])
            submitted = st.form_submit_button("Save")
            if submitted:
                participant = {
                    "number": number,
                    "name": name,
                    "age": age,
                    "agency": agency,
                    "height": height,
                    "waist": waist,
                    "dress_suit": dress_suit,
                    "role": role,
                    "availability": availability,
                    "photo": photo_to_b64(photo.read()) if photo else None
                }
                st.session_state["projects"][current].append(participant)
                save_data()
                log_action(current_user, "add_participant", name)
                st.success(f"✅ {name} added!")
                st.experimental_rerun()

    # Display participants
    project_data = st.session_state["projects"][current]
    cols = st.columns(3)
    for idx, p in enumerate(project_data):
        with cols[idx % 3]:
            color = role_color(p["role"] or "default")
            st.markdown(f"""
            <div class="card">
                <h3>#{p.get("number", idx+1)} {p['name'] or 'Unnamed'}</h3>
                <span class="role-tag" style="background-color:{color}">{p['role']}</span>
            """, unsafe_allow_html=True)

            if p["photo"]:
                image = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
                st.image(image, width=150)

            st.markdown(f"""
            <div class="detail-row"><span class="detail-label">Age:</span> <span class="detail-value">{p['age']}</span></div>
            <div class="detail-row"><span class="detail-label">Agency:</span> <span class="detail-value">{p['agency']}</span></div>
            <div class="detail-row"><span class="detail-label">Height:</span> <span class="detail-value">{p['height']}</span></div>
            <div class="detail-row"><span class="detail-label">Waist:</span> <span class="detail-value">{p['waist']}</span></div>
            <div class="detail-row"><span class="detail-label">Dress/Suit:</span> <span class="detail-value">{p['dress_suit']}</span></div>
            <div class="detail-row"><span class="detail-label">Availability:</span> <span class="detail-value">{p.get('availability','')}</span></div>
            """, unsafe_allow_html=True)

            col1, col2 = st.columns(2)
            with col1:
                if st.button("✏️ Edit", key=f"edit_{idx}"):
                    st.session_state["editing"] = idx
                    st.experimental_rerun()
            with col2:
                if st.button("🗑 Delete", key=f"delete_{idx}"):
                    st.session_state["projects"][current].pop(idx)
                    save_data()
                    log_action(current_user, "delete_participant", p["name"])
                    st.warning("Deleted!")
                    st.experimental_rerun()

            st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state["editing"] is not None:
        edit_idx = st.session_state["editing"]
        if edit_idx < len(project_data):
            p = project_data[edit_idx]
            st.subheader(f"✏️ Edit Participant #{p.get('number','')}")
            with st.form("edit_participant_form"):
                number = st.number_input("Participant #", min_value=1, value=int(p.get("number", edit_idx+1)))
                name = st.text_input("Name", value=p["name"])
                age = st.text_input("Age", value=p["age"])
                agency = st.text_input("Agency", value=p["agency"])
                height = st.text_input("Height", value=p["height"])
                waist = st.text_input("Waist", value=p["waist"])
                dress_suit = st.text_input("Dress/Suit Size", value=p["dress_suit"])
                role = st.text_input("Role/Status", value=p["role"])
                availability = st.text_area("Availability", value=p.get("availability",""))
                photo = st.file_uploader("Upload Picture (leave empty to keep current)", type=["png", "jpg", "jpeg"])
                save_changes = st.form_submit_button("💾 Save Changes")
                cancel = st.form_submit_button("❌ Cancel")
                if save_changes:
                    p.update({
                        "number": number,
                        "name": name,
                        "age": age,
                        "agency": agency,
                        "height": height,
                        "waist": waist,
                        "dress_suit": dress_suit,
                        "role": role,
                        "availability": availability,
                    })
                    if photo:
                        p["photo"] = photo_to_b64(photo.read())
                    st.session_state["projects"][current][edit_idx] = p
                    save_data()
                    log_action(current_user, "edit_participant", name)
                    st.success("Updated successfully!")
                    st.session_state["editing"] = None
                    st.experimental_rerun()
                elif cancel:
                    st.session_state["editing"] = None
                    st.experimental_rerun()

    # Export to Word
    st.subheader("📄 Export Participants (Word)")
    if st.button("Download Word File of current project"):
        if project_data:
            doc = Document()
            doc.add_heading(f"Participants - {current}", 0)
            for p in project_data:
                table = doc.add_table(rows=1, cols=2)
                table.autofit = False
                table.columns[0].width = Inches(1.7)
                table.columns[1].width = Inches(4.5)
                row
