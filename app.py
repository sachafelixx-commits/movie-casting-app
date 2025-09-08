import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib

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
    box-shadow:0 6px 15px rgba(0,0,0,0.08);
    transition: all 0.3s ease-out;
}
.card:hover {
    transform: translateY(-5px);
    box-shadow:0 12px 28px rgba(0,0,0,0.12);
}
.role-tag {
    color:white;
    padding:3px 8px;
    border-radius:10px;
    font-size:12px;
    font-weight:500;
    display:inline-block;
}
.details { font-size: 13px; color: #333; margin-top: 8px; }
.card-actions { margin-top: 10px; }
</style>
""", unsafe_allow_html=True)

# --- Session state ---
if "projects" not in st.session_state:
    st.session_state["projects"] = {"Default Project": []}
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

# --- Functions ---
def role_color(role):
    h = hashlib.md5(role.encode()).hexdigest()
    r = int(h[:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"#{r:02X}{g:02X}{b:02X}"

def next_free_number(participants):
    used = {p["number"] for p in participants}
    n = 1
    while n in used:
        n += 1
    return n

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
            st.rerun()

# Rename/Delete project
with st.sidebar.expander("⚙️ Manage Project"):
    rename_proj = st.text_input("Rename Project", value=current)
    if st.button("Rename Project"):
        if rename_proj and rename_proj not in st.session_state["projects"]:
            st.session_state["projects"][rename_proj] = st.session_state["projects"].pop(current)
            st.session_state["current_project"] = rename_proj
            st.success(f"Renamed to '{rename_proj}'")
            st.rerun()
    if st.button("🗑 Delete Project"):
        if current in st.session_state["projects"] and len(st.session_state["projects"]) > 1:
            st.session_state["projects"].pop(current)
            st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
            st.warning(f"Deleted '{current}'")
            st.rerun()

# --- Sidebar: Add/Edit Participant ---
st.sidebar.subheader(f"➕ Add New Participant to {current}")
with st.sidebar.form("add_participant_form"):
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
        number = next_free_number(st.session_state["projects"][current])
        participant = {
            "number": number,
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
        st.success(f"✅ {name} added as Participant #{number}!")
        st.rerun()

# --- Main Area: Participant Cards ---
st.markdown(f"<h2 style='color:#1E1E1E;'>Participants in {current}</h2>", unsafe_allow_html=True)
project_data = st.session_state["projects"][current]

cols = st.columns(3)
for idx, p in enumerate(project_data):
    with cols[idx % 3]:
        color = role_color(p["role"] or "default")
        st.markdown(f"""
        <div class="card">
            <h3 style="margin-bottom:5px;">#{p['number']} - {p['name'] or 'Unnamed'}</h3>
            <span class="role-tag" style="background-color:{color}">{p['role'] or 'No Role'}</span>
        </div>
        """, unsafe_allow_html=True)

        if p["photo"]:
            image = Image.open(io.BytesIO(p["photo"]))
            st.image(image, width=180)

        st.markdown(f"""
        <div class="details">
        🧑 Age: {p['age']}<br>
        🎭 Agency: {p['agency']}<br>
        📏 Height: {p['height']}<br>
        📐 Waist: {p['waist']}<br>
        👕 Dress/Suit: {p['dress_suit']}
        </div>
        """, unsafe_allow_html=True)

        # Actions: Edit number + Delete
        new_num = st.number_input(
            f"Number for {p['name'] or 'Unnamed'}",
            min_value=1,
            step=1,
            value=p["number"],
            key=f"num_{current}_{idx}"
        )
        if new_num != p["number"]:
            if st.button(f"Save #{new_num}", key=f"save_num_{current}_{idx}"):
                existing_nums = [x["number"] for x in project_data if x is not p]
                if new_num in existing_nums:
                    st.error(f"⚠️ Number {new_num} is already taken.")
                else:
                    p["number"] = new_num
                    st.success(f"✅ Updated number for {p['name']} to #{new_num}")
                    st.rerun()

        if st.button(f"🗑 Delete {p['name'] or 'Unnamed'}", key=f"del_{current}_{idx}"):
            project_data.remove(p)
            st.warning(f"Deleted {p['name'] or 'Unnamed'}")
            st.rerun()

# --- Export ---
st.subheader("📄 Export Participants (Word)")
if st.button("Download Word File of current project"):
    if project_data:
        doc = Document()
        doc.add_heading(f"Participants - {current}", 0)
        for p in sorted(project_data, key=lambda x: x["number"]):
            table = doc.add_table(rows=1, cols=2)
            table.autofit = False
            table.columns[0].width = Inches(1.7)
            table.columns[1].width = Inches(4.5)
            row_cells = table.rows[0].cells

            if p['photo']:
                from io import BytesIO
                image_stream = BytesIO(p['photo'])
                try:
                    paragraph = row_cells[0].paragraphs[0]
                    run = paragraph.add_run()
                    run.add_picture(image_stream, width=Inches(1.5))
                except:
                    row_cells[0].text = "No Photo"
            else:
                row_cells[0].text = "No Photo"

            info_text = (
                f"#{p['number']} - {p['name'] or 'Unnamed'}\n"
                f"Role: {p['role']}\nAge: {p['age']}\nAgency: {p['agency']}\n"
                f"Height: {p['height']}\nWaist: {p['waist']}\nDress/Suit: {p['dress_suit']}"
            )
            row_cells[1].text = info_text
            doc.add_paragraph("\n")

        from io import BytesIO
        word_stream = BytesIO()
        doc.save(word_stream)
        word_stream.seek(0)

        st.download_button(
            label="⬇ Download Word file",
            data=word_stream,
            file_name=f"{current}_participants.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    else:
        st.info("No participants in this project yet.")
