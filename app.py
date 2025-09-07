import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches

# --- App setup ---
st.set_page_config(page_title="Movie Casting Manager", layout="wide")
st.markdown("""
<h1 style='text-align: center; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color:#1E1E1E;'>
🎬 Movie Casting Manager
</h1>
""", unsafe_allow_html=True)

# --- Session state ---
if "projects" not in st.session_state:
    st.session_state["projects"] = {"Default Project": []}
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

# --- Sidebar for projects ---
st.sidebar.header("📂 Project Manager")
project_names = list(st.session_state["projects"].keys())
current = st.sidebar.selectbox("Select Project", project_names, index=project_names.index(st.session_state["current_project"]))
st.session_state["current_project"] = current

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

# --- Add Participant ---
st.markdown(f"<h2 style='font-family: -apple-system, BlinkMacSystemFont; color:#1E1E1E;'>Participants in {current}</h2>", unsafe_allow_html=True)
with st.expander("➕ Add New Participant"):
    with st.form("add_participant_form"):
        name = st.text_input("Name")
        age = st.text_input("Age")
        agency = st.text_input("Agency")
        height = st.text_input("Height")
        waist = st.text_input("Waist")
        dress_suit = st.text_input("Dress/Suit Size")
        photo = st.file_uploader("Upload Picture", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("Save")
        if submitted:
            participant = {
                "name": name,
                "age": age,
                "agency": agency,
                "height": height,
                "waist": waist,
                "dress_suit": dress_suit,
                "photo": photo.read() if photo else None
            }
            st.session_state["projects"][current].append(participant)
            st.success(f"✅ {name} added!")

# --- Display participants in modern Apple-style cards ---
project_data = st.session_state["projects"][current]
cols = st.columns(3)

for idx, p in enumerate(project_data):
    with cols[idx % 3]:
        st.markdown(f"""
        <div style="
            background-color:#F5F5F7;
            border-radius:20px;
            padding:15px;
            margin-bottom:15px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        ">
            <h3 style="color:#1E1E1E; margin-bottom:5px;">{p['name'] or 'Unnamed'}</h3>
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

        # Edit participant
        with st.expander("✏️ Edit Participant"):
            with st.form(f"edit_form_{idx}"):
                p["name"] = st.text_input("Name", value=p["name"])
                p["age"] = st.text_input("Age", value=p["age"])
                p["agency"] = st.text_input("Agency", value=p["agency"])
                p["height"] = st.text_input("Height", value=p["height"])
                p["waist"] = st.text_input("Waist", value=p["waist"])
                p["dress_suit"] = st.text_input("Dress/Suit", value=p["dress_suit"])
                new_photo = st.file_uploader("Upload new photo", type=["png", "jpg", "jpeg"], key=f"photo_{idx}")
                if new_photo:
                    p["photo"] = new_photo.read()
                save_changes = st.form_submit_button("Save changes")
                if save_changes:
                    st.success(f"✅ Updated {p['name']}")

        # Delete participant
        if st.button(f"🗑 Delete {p['name'] or 'Participant'}", key=f"del_{idx}"):
            st.session_state["projects"][current].pop(idx)
            st.warning("Participant removed")
            st.experimental_rerun()

# --- Export to Word (.docx) ---
st.subheader("📄 Export Participants (Word)")
if st.button("Download Word File of current project"):
    if project_data:
        doc = Document()
        doc.add_heading(f"Participants - {current}", 0)
        for p in project_data:
            doc.add_heading(p['name'] or "Unnamed", level=1)
            doc.add_paragraph(f"Age: {p['age']}")
            doc.add_paragraph(f"Agency: {p['agency']}")
            doc.add_paragraph(f"Height: {p['height']}")
            doc.add_paragraph(f"Waist: {p['waist']}")
            doc.add_paragraph(f"Dress/Suit: {p['dress_suit']}")
            if p['photo']:
                try:
                    from io import BytesIO
                    image_stream = BytesIO(p['photo'])
                    doc.add_picture(image_stream, width=Inches(1.5))
                except Exception as e:
                    print("Error adding image:", e)
            doc.add_paragraph("---------------------------")

        from io import BytesIO
        word_stream = BytesIO()
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
