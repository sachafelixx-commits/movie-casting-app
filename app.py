import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import time

# --- Page setup ---
st.set_page_config(page_title="🎬 Movie Casting Manager", layout="wide")

# --- CSS ---
st.markdown("""
<style>
body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F8F9FA; }
.card {
    background-color:#FFFFFF;
    border-radius:20px;
    padding:15px;
    margin-bottom:20px;
    box-shadow:0 8px 20px rgba(0,0,0,0.08);
    opacity:0;
    transform: translateY(20px);
    transition: all 0.5s ease-out;
}
.card.visible {
    opacity:1;
    transform: translateY(0);
}
.card:hover {
    transform: translateY(-5px);
    box-shadow:0 12px 28px rgba(0,0,0,0.12);
}
.role-tag {
    color:white;
    padding:4px 10px;
    border-radius:12px;
    font-size:13px;
    font-weight:500;
    display:inline-block;
}
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 style='text-align:center; color:#1E1E1E;'>🎬 Movie Casting Manager</h1>", unsafe_allow_html=True)

# --- Session state ---
if "projects" not in st.session_state:
    st.session_state["projects"] = {"Default Project": []}
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

# --- Sidebar Project Manager ---
st.sidebar.header("📂 Project Manager")
project_names = list(st.session_state["projects"].keys())
selected_project = st.sidebar.selectbox("Select Project", project_names, index=project_names.index(st.session_state["current_project"]))

# Smooth transition: fade-out old cards if project changed
if selected_project != st.session_state["current_project"]:
    container = st.empty()
    with container:
        st.write("Switching projects...")
    time.sleep(0.3)  # short fade-out delay
    st.session_state["current_project"] = selected_project
    st.experimental_rerun()  # triggers fade-in of new project

current = st.session_state["current_project"]

# Add new project
with st.sidebar.expander("➕ Create Project"):
    new_proj = st.text_input("Project name")
    if st.button("Add Project"):
        if new_proj and new_proj not in st.session_state["projects"]:
            st.session_state["projects"][new_proj] = []
            st.session_state["current_project"] = new_proj
            st.success(f"Project '{new_proj}' added!")
            st.experimental_rerun()

# --- Dynamic role color ---
def role_color(role):
    h = hashlib.md5(role.encode()).hexdigest()
    r = int(h[:2],16)
    g = int(h[2:4],16)
    b = int(h[4:6],16)
    return f"#{r:02X}{g:02X}{b:02X}"

# --- Add Participant ---
st.markdown(f"<h2 style='color:#1E1E1E;'>Participants in {current}</h2>", unsafe_allow_html=True)
with st.expander("➕ Add New Participant"):
    with st.form("add_participant_form"):
        name = st.text_input("Name")
        age = st.text_input("Age")
        agency = st.text_input("Agency")
        height = st.text_input("Height")
        waist = st.text_input("Waist")
        dress_suit = st.text_input("Dress/Suit Size")
        role = st.text_input("Role/Status")
        photo = st.file_uploader("Upload Picture", type=["png","jpg","jpeg"])
        submitted = st.form_submit_button("Save")
        if submitted:
            participant = {"name":name,"age":age,"agency":agency,"height":height,"waist":waist,"dress_suit":dress_suit,"role":role,"photo":photo.read() if photo else None}
            st.session_state["projects"][current].append(participant)
            st.success(f"✅ {name} added!")
            st.experimental_rerun()

# --- Display Participants with fade-in animation ---
project_data = st.session_state["projects"][current]
cols = st.columns(3)
for idx, p in enumerate(project_data):
    with cols[idx%3]:
        color = role_color(p["role"] or "default")
        st.markdown(f"""
        <div class="card visible">
            <h3 style="margin-bottom:5px;">{p['name'] or 'Unnamed'}</h3>
            <span class="role-tag" style="background-color:{color}">{p['role']}</span>
        </div>
        """, unsafe_allow_html=True)
        if p["photo"]:
            image = Image.open(io.BytesIO(p["photo"]))
            st.image(image, width=150)

        st.markdown(f"""
        **Age:** {p['age']}  
        **Agency:** {p['agency']}  
        **Height:** {p['height']}  
        **Waist:** {p['waist']}  
        **Dress/Suit:** {p['dress_suit']}  
        """)
