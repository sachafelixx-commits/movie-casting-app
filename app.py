import streamlit as st
from PIL import Image
import io

st.set_page_config(page_title="Movie Casting App", layout="wide")

# --- Session State Setup ---
if "projects" not in st.session_state:
    st.session_state["projects"] = {}  # project_name -> list of participants
if "current_project" not in st.session_state:
    st.session_state["current_project"] = None

# --- Helper: Role Colors ---
def role_color(role):
    colors = {
        "Lead": "#FF6961",
        "Supporting": "#77DD77",
        "Extra": "#AEC6CF",
        "default": "#FFD580",
    }
    return colors.get(role, colors["default"])

# --- Sidebar: Project Management ---
st.sidebar.header("🎬 Project Manager")
project_names = list(st.session_state["projects"].keys())

new_project = st.sidebar.text_input("Create New Project")
if st.sidebar.button("➕ Add Project") and new_project:
    if new_project not in st.session_state["projects"]:
        st.session_state["projects"][new_project] = []
        st.session_state["current_project"] = new_project

if project_names:
    choice = st.sidebar.radio("Select Project", project_names)
    st.session_state["current_project"] = choice

# --- Main Area ---
if st.session_state["current_project"]:
    current = st.session_state["current_project"]
    st.title(f"🎬 Project: {current}")

    # --- Add Participant ---
    st.subheader("➕ Add Participant")
    with st.form("add_participant"):
        name = st.text_input("Name")
        age = st.text_input("Age")
        agency = st.text_input("Agency")
        height = st.text_input("Height")
        waist = st.text_input("Waist")
        dress_suit = st.text_input("Dress/Suit")
        role = st.selectbox("Role", ["Lead", "Supporting", "Extra"])
        photo = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png"])
        submitted = st.form_submit_button("Add Participant")

        if submitted:
            if photo:
                img_bytes = photo.read()
            else:
                img_bytes = None

            participants = st.session_state["projects"][current]
            new_number = len(participants) + 1  # auto-numbering
            st.session_state["projects"][current].append({
                "name": name,
                "age": age,
                "agency": agency,
                "height": height,
                "waist": waist,
                "dress_suit": dress_suit,
                "role": role,
                "photo": img_bytes,
                "number": new_number
            })
            st.success(f"✅ Added {name} to {current}")
            st.experimental_rerun()

    # --- Participants Display ---
    st.subheader("👥 Participants")

    # Auto-renumber button
    if st.button("🔄 Auto-Renumber Participants"):
        for i, p in enumerate(st.session_state["projects"][current], start=1):
            p["number"] = i
        st.success("✅ All participants have been renumbered!")
        st.experimental_rerun()

    project_data = st.session_state["projects"][current]

    # --- Always sort participants by number before displaying ---
    project_data = sorted(project_data, key=lambda x: x.get("number", 99999))

    cols = st.columns(3)
    for idx, p in enumerate(project_data):
        with cols[idx % 3]:
            color = role_color(p["role"] or "default")

            # Editable number
            new_number = st.number_input(
                f"Number for {p['name'] or 'Unnamed'}", 
                value=p["number"], 
                step=1, 
                key=f"num_{idx}"
            )
            p["number"] = new_number

            st.markdown(f"""
            <div style="border:1px solid #ddd; padding:10px; border-radius:8px; margin-bottom:10px;">
                <h3 style="margin-bottom:5px;">#{p['number']} - {p['name'] or 'Unnamed'}</h3>
                <span style="background-color:{color}; padding:3px 8px; border-radius:5px; color:white;">
                    {p['role']}
                </span>
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

else:
    st.title("🎬 Movie Casting App")
    st.info("➡️ Create or select a project in the sidebar to begin.")
