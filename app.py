import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import json
import base64
import os
from datetime import date

# ------------------------
# Files for data persistence
# ------------------------
DATA_FILE = "casting_data.json"
USERS_FILE = "users.json"

# ------------------------
# Helper functions
# ------------------------
def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(st.session_state["projects"], f)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"Default Project": []}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def photo_to_b64(photo_bytes):
    return base64.b64encode(photo_bytes).decode("utf-8")

def b64_to_photo(b64_str):
    return base64.b64decode(b64_str)

def role_color(role):
    h = hashlib.md5(role.encode()).hexdigest()
    r = int(h[:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"#{r:02X}{g:02X}{b:02X}"

# ------------------------
# Streamlit page setup
# ------------------------
st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")
st.markdown("""
<style>
.card { background-color:#FAFAFA; border-radius:20px; padding:20px; margin-bottom:20px; box-shadow:0 6px 16px rgba(0,0,0,0.06);}
.card h3 { margin-bottom:8px; font-size:20px; font-weight:600; color:#222; }
.role-tag { color:white; padding:4px 10px; border-radius:12px; font-size:13px; font-weight:500; display:inline-block; }
.detail-label { font-weight:600; color:#555; }
.detail-value { color:#222; font-weight:400; }
.detail-row { border-top:1px solid #EEE; padding-top:6px; margin-top:6px; }
</style>
""", unsafe_allow_html=True)

# ------------------------
# Initialize session state
# ------------------------
if "page" not in st.session_state: st.session_state["page"] = "auth"
if "auth_mode" not in st.session_state: st.session_state["auth_mode"] = "login"  # login or signup
if "logged_in" not in st.session_state: st.session_state["logged_in"] = False
if "projects" not in st.session_state: st.session_state["projects"] = load_data()
if "current_project" not in st.session_state: st.session_state["current_project"] = "Default Project"
if "editing" not in st.session_state: st.session_state["editing"] = None
users = load_users()

# ------------------------
# Authentication Page
# ------------------------
if st.session_state["page"] == "auth":
    st.title("🎬 Sacha's Casting Manager")
    if st.session_state["auth_mode"] == "login":
        st.subheader("Login")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            if username in users and users[username] == hash_password(password):
                st.session_state["logged_in"] = True
                st.session_state["page"] = "main"
                st.success("Login successful!")
                st.experimental_rerun()
            else:
                st.error("Invalid credentials")
        st.write("Don't have an account? [Sign Up](#)", unsafe_allow_html=True)
        if st.button("Switch to Sign Up"):
            st.session_state["auth_mode"] = "signup"
            st.experimental_rerun()
    else:
        st.subheader("Sign Up")
        new_user = st.text_input("Choose a Username")
        new_pass = st.text_input("Choose a Password", type="password")
        if st.button("Sign Up"):
            if new_user in users:
                st.error("Username already exists")
            elif new_user and new_pass:
                users[new_user] = hash_password(new_pass)
                save_users(users)
                st.success("Sign up successful! You can now log in.")
                st.session_state["auth_mode"] = "login"
                st.experimental_rerun()
            else:
                st.warning("Enter both username and password")
        if st.button("Switch to Login"):
            st.session_state["auth_mode"] = "login"
            st.experimental_rerun()
    st.stop()

# ------------------------
# Main App
# ------------------------
if not st.session_state["logged_in"]:
    st.stop()

# ------------------------
# Sidebar: project manager
# ------------------------
st.sidebar.header("📂 Project Manager")
project_names = list(st.session_state["projects"].keys())
selected_project = st.sidebar.selectbox(
    "Select Project", project_names, index=project_names.index(st.session_state["current_project"])
)
st.session_state["current_project"] = selected_project
current = st.session_state["current_project"]

with st.sidebar.expander("➕ Create Project"):
    new_proj = st.text_input("Project name")
    if st.button("Add Project"):
        if new_proj and new_proj not in st.session_state["projects"]:
            st.session_state["projects"][new_proj] = []
            st.session_state["current_project"] = new_proj
            save_data()
            st.success(f"Project '{new_proj}' added!")
            st.experimental_rerun()

with st.sidebar.expander("⚙️ Manage Project"):
    rename_proj = st.text_input("Rename Project", value=current)
    if st.button("Rename Project"):
        if rename_proj and rename_proj not in st.session_state["projects"]:
            st.session_state["projects"][rename_proj] = st.session_state["projects"].pop(current)
            st.session_state["current_project"] = rename_proj
            save_data()
            st.success(f"Renamed to '{rename_proj}'")
            st.experimental_rerun()
    if st.button("🗑 Delete Project"):
        if current in st.session_state["projects"] and len(st.session_state["projects"]) > 1:
            st.session_state["projects"].pop(current)
            st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
            save_data()
            st.warning(f"Deleted '{current}'")
            st.experimental_rerun()

# ------------------------
# Add participant
# ------------------------
st.markdown(f"## Participants in {current} ({len(st.session_state['projects'][current])})")
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
        availability = st.date_input("Next Available Date", value=date.today())
        photo = st.file_uploader("Upload Picture", type=["png","jpg","jpeg"])
        submitted = st.form_submit_button("Save")
        if submitted:
            participant = {
                "number": number, "name": name, "age": age, "agency": agency,
                "height": height, "waist": waist, "dress_suit": dress_suit,
                "role": role, "availability": str(availability),
                "photo": photo_to_b64(photo.read()) if photo else None
            }
            st.session_state["projects"][current].append(participant)
            save_data()
            st.success(f"✅ {name} added!")
            st.experimental_rerun()

# ------------------------
# Display participants with availability
# ------------------------
project_data = st.session_state["projects"][current]
cols = st.columns(3)
for idx, p in enumerate(project_data):
    with cols[idx % 3]:
        color = role_color(p["role"] or "default")
        st.markdown(f"<div class='card'><h3>#{p.get('number', idx+1)} {p['name'] or 'Unnamed'}</h3><span class='role-tag' style='background-color:{color}'>{p['role']}</span>", unsafe_allow_html=True)

        if p["photo"]:
            image = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
            st.image(image, width=150)
        else:
            st.image("https://via.placeholder.com/150?text=No+Photo", width=150)

        st.markdown(f"""
        <div class='detail-row'><span class='detail-label'>Age:</span> <span class='detail-value'>{p['age']}</span></div>
        <div class='detail-row'><span class='detail-label'>Agency:</span> <span class='detail-value'>{p['agency']}</span></div>
        <div class='detail-row'><span class='detail-label'>Height:</span> <span class='detail-value'>{p['height']}</span></div>
        <div class='detail-row'><span class='detail-label'>Waist:</span> <span class='detail-value'>{p['waist']}</span></div>
        <div class='detail-row'><span class='detail-label'>Dress/Suit:</span> <span class='detail-value'>{p['dress_suit']}</span></div>
        <div class='detail-row'><span class='detail-label'>Next Available:</span> <span class='detail-value'>{p['availability']}</span></div>
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
                st.warning("Deleted!")
                st.experimental_rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------
# Edit participant inline
# ------------------------
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
            availability = st.date_input("Next Available Date", value=date.fromisoformat(p["availability"]))
            photo = st.file_uploader("Upload Picture (leave empty to keep current)", type=["png","jpg","jpeg"])
            save_changes = st.form_submit_button("💾 Save Changes")
            cancel = st.form_submit_button("❌ Cancel")

            if save_changes:
                p.update({
                    "number": number, "name": name, "age": age, "agency": agency,
                    "height": height, "waist": waist, "dress_suit": dress_suit,
                    "role": role, "availability": str(availability)
                })
                if photo:
                    p["photo"] = photo_to_b64(photo.read())
                st.session_state["projects"][current][edit_idx] = p
                save_data()
                st.success("Updated successfully!")
                st.session_state["editing"] = None
                st.experimental_rerun()
            elif cancel:
                st.session_state["editing"] = None
                st.experimental_rerun()

# ------------------------
# Export to Word
# ------------------------
st.subheader("📄 Export Participants")
if st.button("Download Word File of current project"):
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
                try:
                    paragraph = row_cells[0].paragraphs[0]
                    run = paragraph.add_run()
                    run.add_picture(image_stream, width=Inches(1.5))
                except:
                    row_cells[0].text = "No Photo"
            else:
                row_cells[0].text = "No Photo"

            info_text = f"Number: {p.get('number','')}\nName: {p['name'] or 'Unnamed'}\nRole: {p['role']}\nAge: {p['age']}\nAgency: {p['agency']}\nHeight: {p['height']}\nWaist: {p['waist']}\nDress/Suit: {p['dress_suit']}\nNext Available: {p['availability']}"
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
