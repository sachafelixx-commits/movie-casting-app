import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import json
import base64
import os

# ------------------------
# Helpers for persistence
# ------------------------
DATA_FILE = "casting_data.json"

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(st.session_state["projects"], f)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"Default Project": []}

# Convert photo bytes to base64 string
def photo_to_b64(photo_bytes):
    return base64.b64encode(photo_bytes).decode("utf-8")

# Convert base64 string back to photo bytes
def b64_to_photo(b64_str):
    return base64.b64decode(b64_str)

# ------------------------
# Page setup
# ------------------------
st.set_page_config(page_title="🎬 Movie Casting Manager", layout="wide")

# Custom CSS
st.markdown("""
<style>
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background-color: #F8F9FA;
}
.card {
    background-color: #FAFAFA;
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 6px 16px rgba(0,0,0,0.06);
    transition: all 0.3s ease-out;
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
.detail-label {
    font-weight: 600;
    color: #555;
}
.detail-value {
    color: #222;
    font-weight: 400;
}
.detail-row {
    border-top: 1px solid #EEE;
    padding-top: 6px;
    margin-top: 6px;
}
</style>
""", unsafe_allow_html=True)

# ------------------------
# Load / init session state
# ------------------------
if "projects" not in st.session_state:
    st.session_state["projects"] = load_data()
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

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

# Add new project
with st.sidebar.expander("➕ Create Project"):
    new_proj = st.text_input("Project name")
    if st.button("Add Project"):
        if new_proj and new_proj not in st.session_state["projects"]:
            st.session_state["projects"][new_proj] = []
            st.session_state["current_project"] = new_proj
            save_data()
            st.success(f"Project '{new_proj}' added!")
            st.rerun()

# Rename/Delete project
with st.sidebar.expander("⚙️ Manage Project"):
    rename_proj = st.text_input("Rename Project", value=current)
    if st.button("Rename Project"):
        if rename_proj and rename_proj not in st.session_state["projects"]:
            st.session_state["projects"][rename_proj] = st.session_state["projects"].pop(current)
            st.session_state["current_project"] = rename_proj
            save_data()
            st.success(f"Renamed to '{rename_proj}'")
            st.rerun()
    if st.button("🗑 Delete Project"):
        if current in st.session_state["projects"] and len(st.session_state["projects"]) > 1:
            st.session_state["projects"].pop(current)
            st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
            save_data()
            st.warning(f"Deleted '{current}'")
            st.rerun()

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
# Add participant
# ------------------------
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
                "photo": photo_to_b64(photo.read()) if photo else None
            }
            st.session_state["projects"][current].append(participant)
            save_data()
            st.success(f"✅ {name} added!")
            st.rerun()

# ------------------------
# Display participants
# ------------------------
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

        # Photo
        if p["photo"]:
            image = Image.open(io.BytesIO(b64_to_photo(p["photo"])))
            st.image(image, width=150)

        # Details
        st.markdown(f"""
        <div class="detail-row"><span class="detail-label">Age:</span> <span class="detail-value">{p['age']}</span></div>
        <div class="detail-row"><span class="detail-label">Agency:</span> <span class="detail-value">{p['agency']}</span></div>
        <div class="detail-row"><span class="detail-label">Height:</span> <span class="detail-value">{p['height']}</span></div>
        <div class="detail-row"><span class="detail-label">Waist:</span> <span class="detail-value">{p['waist']}</span></div>
        <div class="detail-row"><span class="detail-label">Dress/Suit:</span> <span class="detail-value">{p['dress_suit']}</span></div>
        </div>
        """, unsafe_allow_html=True)

# ------------------------
# Export to Word
# ------------------------
st.subheader("📄 Export Participants (Word - Apple Style)")
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

            info_text = f"Number: {p.get('number','')}\nName: {p['name'] or 'Unnamed'}\nRole: {p['role']}\nAge: {p['age']}\nAgency: {p['agency']}\nHeight: {p['height']}\nWaist: {p['waist']}\nDress/Suit: {p['dress_suit']}"
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
