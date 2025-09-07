import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import base64

# --- Page setup ---
st.set_page_config(page_title="🎬 Movie Casting Manager", layout="wide")

# --- CSS for Apple-style look + Safari fixes ---
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
    -webkit-transition: all 0.5s ease-out; 
    transition: all 0.5s ease-out;
}
.card.visible {
    opacity:1;
    transform: translateY(0);
    -webkit-transform: translateY(0);
}
.card:hover {
    transform: translateY(-5px);
    -webkit-transform: translateY(-5px);
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
            st.success(f"Project '{new_proj}' added!")

# Rename/Delete Project
with st.sidebar.expander("⚙️ Manage Project"):
    rename_proj = st.text_input("Rename Project", value=current)
    if st.button("Rename Project"):
        if rename_proj and rename_proj not in st.session_state["projects"]:
            st.session_state["projects"][rename_proj] = st.session_state["projects"].pop(current)
            st.session_state["current_project"] = rename_proj
            st.success(f"Renamed to '{rename_proj}'")
    if st.button("🗑 Delete Project"):
        if current in st.session_state["projects"] and len(st.session_state["projects"]) > 1:
            st.session_state["projects"].pop(current)
            st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
            st.warning(f"Deleted '{current}'")

# --- Role color ---
def role_color(role):
    h = hashlib.md5(role.encode()).hexdigest()
    r = int(h[:2],16)
    g = int(h[2:4],16)
    b = int(h[4:6],16)
    return f"#{r:02X}{g:02X}{b:02X}"

# --- Add participant ---
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
            participant = {
                "name": name,
                "age": age,
                "agency": agency,
                "height": height,
                "waist": waist,
                "dress_suit": dress_suit,
                "role": role,
                "photo": photo.read() if photo else None
            }
            st.session_state["projects"][current].append(participant)
            st.success(f"✅ {name} added!")

# --- Display participants (responsive layout) ---
project_data = st.session_state["projects"][current]

# Detect mobile screen: assume width < 500 pixels -> 1 column
is_mobile = st.experimental_get_query_params().get("mobile", ["0"])[0] == "1"
num_cols = 1 if is_mobile else 3
cols = st.columns(num_cols)

for idx, p in enumerate(project_data):
    with cols[idx % num_cols]:
        color = role_color(p["role"] or "default")
        st.markdown(f"""
        <div class="card visible">
            <h3 style="margin-bottom:5px;">{p['name'] or 'Unnamed'}</h3>
            <span class="role-tag" style="background-color:{color}">{p['role']}</span>
        </div>
        """, unsafe_allow_html=True)
        if p["photo"]:
            image = Image.open(io.BytesIO(p["photo"]))
            image.thumbnail((300, 300))  # mobile-friendly
            st.image(image, width=150)

        st.markdown(f"""
        **Age:** {p['age']}  
        **Agency:** {p['agency']}  
        **Height:** {p['height']}  
        **Waist:** {p['waist']}  
        **Dress/Suit:** {p['dress_suit']}  
        """)

# --- Export to Word via base64 (Safari-safe) ---
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

            if p['photo']:
                image_stream = io.BytesIO(p['photo'])
                try:
                    paragraph = row_cells[0].paragraphs[0]
                    run = paragraph.add_run()
                    run.add_picture(image_stream, width=Inches(1.5))
                except:
                    row_cells[0].text = "No Photo"
            else:
                row_cells[0].text = "No Photo"

            info_text = f"Name: {p['name'] or 'Unnamed'}\nRole: {p['role']}\nAge: {p['age']}\nAgency: {p['agency']}\nHeight: {p['height']}\nWaist: {p['waist']}\nDress/Suit: {p['dress_suit']}"
            row_cells[1].text = info_text
            doc.add_paragraph("\n")

        word_stream = io.BytesIO()
        doc.save(word_stream)
        word_stream.seek(0)

        # Base64 download link (works on iOS Safari)
        b64 = base64.b64encode(word_stream.read()).decode()
        href = f'<a href="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{b64}" download="{current}_participants.docx">📥 Download Word File</a>'
        st.markdown(href, unsafe_allow_html=True)
    else:
        st.info("No participants in this project yet.")
