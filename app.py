# app.py
import streamlit as st
from io import BytesIO
from docx import Document
from PIL import Image

# ------------------------------
# Page config
# ------------------------------
st.set_page_config(page_title="Movie Casting Manager", layout="centered")

# ------------------------------
# Session state for projects
# ------------------------------
if "projects" not in st.session_state:
    st.session_state.projects = {"Default Project": []}

if "current_project" not in st.session_state:
    st.session_state.current_project = "Default Project"

# ------------------------------
# Helper functions
# ------------------------------
def add_project(name):
    if name and name not in st.session_state.projects:
        st.session_state.projects[name] = []
        st.session_state.current_project = name

def rename_project(new_name):
    if new_name and new_name not in st.session_state.projects:
        st.session_state.projects[new_name] = st.session_state.projects.pop(st.session_state.current_project)
        st.session_state.current_project = new_name

def delete_project():
    if len(st.session_state.projects) > 1:
        st.session_state.projects.pop(st.session_state.current_project)
        st.session_state.current_project = list(st.session_state.projects.keys())[0]

def add_participant(name, role, age, agency, height, waist, dress, photo):
    st.session_state.projects[st.session_state.current_project].append({
        "name": name,
        "role": role,
        "age": age,
        "agency": agency,
        "height": height,
        "waist": waist,
        "dress": dress,
        "photo": photo
    })

def export_to_word():
    doc = Document()
    doc.add_heading(f'Participants - {st.session_state.current_project}', level=1)
    for p in st.session_state.projects[st.session_state.current_project]:
        if p["photo"]:
            image = Image.open(p["photo"])
            img_bytes = BytesIO()
            image.save(img_bytes, format='PNG')
            doc.add_picture(BytesIO(img_bytes.getvalue()))
        doc.add_paragraph(f'Name: {p["name"]}')
        doc.add_paragraph(f'Role: {p["role"]}')
        doc.add_paragraph(f'Age: {p["age"]}')
        doc.add_paragraph(f'Agency: {p["agency"]}')
        doc.add_paragraph(f'Height: {p["height"]}')
        doc.add_paragraph(f'Waist: {p["waist"]}')
        doc.add_paragraph(f'Dress/Suit: {p["dress"]}')
        doc.add_paragraph('---------------------------')
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

# ------------------------------
# Sidebar - Project Manager
# ------------------------------
st.sidebar.header("Project Manager")
projects = list(st.session_state.projects.keys())
selected_project = st.sidebar.selectbox("Select Project", projects, index=projects.index(st.session_state.current_project))
st.session_state.current_project = selected_project

new_project_name = st.sidebar.text_input("New Project Name")
if st.sidebar.button("Add Project"):
    add_project(new_project_name)

rename_name = st.sidebar.text_input("Rename Project")
if st.sidebar.button("Rename Project"):
    rename_project(rename_name)

if st.sidebar.button("Delete Project"):
    delete_project()

# ------------------------------
# Participant Input
# ------------------------------
st.header(f"Project: {st.session_state.current_project}")

with st.form("participant_form"):
    name = st.text_input("Name")
    role = st.text_input("Role")
    age = st.text_input("Age")
    agency = st.text_input("Agency")
    height = st.text_input("Height")
    waist = st.text_input("Waist")
    dress = st.text_input("Dress/Suit")
    photo = st.file_uploader("Photo", type=["png", "jpg", "jpeg"])
    submitted = st.form_submit_button("Add Participant")
    if submitted:
        add_participant(name, role, age, agency, height, waist, dress, photo)

# ------------------------------
# Display Participants
# ------------------------------
st.subheader("Participants")
for p in st.session_state.projects[st.session_state.current_project]:
    st.write(f"**Name:** {p['name']}")
    st.write(f"**Role:** {p['role']}")
    st.write(f"**Age:** {p['age']}")
    st.write(f"**Agency:** {p['agency']}")
    st.write(f"**Height:** {p['height']}")
    st.write(f"**Waist:** {p['waist']}")
    st.write(f"**Dress/Suit:** {p['dress']}")
    if p["photo"]:
        st.image(p["photo"], width=150)
    st.markdown("---")

# ------------------------------
# Export Button
# ------------------------------
buffer = export_to_word()
st.download_button(
    label="Export Participants to Word",
    data=buffer,
    file_name=f"{st.session_state.current_project}_participants.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
