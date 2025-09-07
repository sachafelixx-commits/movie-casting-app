import streamlit as st
from PIL import Image
import io

# --- App setup ---
st.set_page_config(page_title="Movie Casting App", layout="wide")
st.title("🎥 Movie Casting Application")

# Session state to persist data
if "projects" not in st.session_state:
    st.session_state["projects"] = {"Project A": [], "Project B": [], "Project C": []}

# Sidebar project selector
project = st.sidebar.selectbox("Select a Project", list(st.session_state["projects"].keys()))

st.sidebar.write(f"Currently working on: **{project}**")

# --- Add new participant ---
with st.expander("➕ Add a new participant"):
    with st.form("add_participant_form"):
        name = st.text_input("Name")
        age = st.text_input("Age")
        agency = st.text_input("Agency")
        height = st.text_input("Height")
        waist = st.text_input("Waist")
        dress_suit = st.text_input("Dress/Suit Size")
        photo = st.file_uploader("Upload Picture", type=["png", "jpg", "jpeg"])

        submitted = st.form_submit_button("Save Participant")
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
            st.session_state["projects"][project].append(participant)
            st.success(f"✅ {name} added to {project}")

# --- Display participants ---
st.subheader(f"Participants in {project}")
cols = st.columns(3)

for idx, p in enumerate(st.session_state["projects"][project]):
    with cols[idx % 3]:
        st.markdown("---")
        if p["photo"]:
            image = Image.open(io.BytesIO(p["photo"]))
            st.image(image, width=150)
        st.markdown(f"""
        **{p['name']}**  
        Age: {p['age']}  
        Agency: {p['agency']}  
        Height: {p['height']}  
        Waist: {p['waist']}  
        Dress/Suit: {p['dress_suit']}  
        """)
