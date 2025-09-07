import streamlit as st
from PIL import Image
import io
import pandas as pd

# --- App setup ---
st.set_page_config(page_title="🎥 Movie Casting Manager", layout="wide")
st.markdown("<h1 style='text-align: center; color: #4B0082;'>🎬 Movie Casting Manager</h1>", unsafe_allow_html=True)

# --- Session state ---
if "projects" not in st.session_state:
    st.session_state["projects"] = {"Default Project": []}
if "current_project" not in st.session_state:
    st.session_state["current_project"] = "Default Project"

# --- Sidebar controls ---
st.sidebar.header("📂 Project Manager")
project_names = list(st.session_state["projects"].keys())
current = st.sidebar.selectbox("Select a project", project_names, 
                               index=project_names.index(st.session_state["current_project"]))
st.session_state["current_project"] = current

with st.sidebar.expander("➕ Create new project"):
    new_proj = st.text_input("Project name")
    if st.button("Add Project"):
        if new_proj and new_proj not in st.session_state["projects"]:
            st.session_state["projects"][new_proj] = []
            st.session_state["current_project"] = new_proj
            st.success(f"Project '{new_proj}' created!")

with st.sidebar.expander("⚙️ Manage project"):
    rename_proj = st.text_input("Rename current project", value=current)
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

# --- Add participant ---
st.markdown(f"<h2 style='color: #4B0082;'>Participants in {st.session_state['current_project']}</h2>", unsafe_allow_html=True)
with st.expander("➕ Add new participant"):
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
                "photo": photo.read() if photo else None,
            }
            st.session_state["projects"][st.session_state["current_project"]].append(participant)
            st.success(f"✅ {name} added!")

# --- Display participants in polished cards ---
project_data = st.session_state["projects"][st.session_state["current_project"]]
columns = st.columns(3)
for idx, p in enumerate(project_data):
    with columns[idx % 3]:
        st.markdown(f"""
        <div style="background-color:#E6E6FA; padding:10px; border-radius:10px; margin-bottom:10px;">
        <h3 style="color:#4B0082;">{p['name'] or 'Unnamed'}</h3>
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
        with st.expander("✏️ Edit participant"):
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
            st.session_state["projects"][st.session_state["current_project"]].pop(idx)
            st.warning("Participant removed")
            st.experimental_rerun()

# --- Download CSV ---
st.subheader("📥 Export Participants")
if st.button("Download CSV of current project"):
    if project_data:
        df = pd.DataFrame(project_data)
        if "photo" in df.columns:
            df["photo"] = df["photo"].apply(lambda x: "Uploaded" if x else "No Photo")
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Click to download CSV",
            data=csv,
            file_name=f"{st.session_state['current_project']}_participants.csv",
            mime='text/csv'
        )
    else:
        st.info("No participants in this project yet.")
