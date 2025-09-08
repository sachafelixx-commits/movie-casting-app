import streamlit as st
st.write("✅ Streamlit version:", st.__version__)

import streamlit as st
from PIL import Image, ImageOps
import io
from docx import Document
from docx.shared import Inches
import hashlib
import json
import base64
import os

# -------------------------
# Config & helpers
# -------------------------
DATA_FILE = "projects.json"  # persisted storage file

st.set_page_config(page_title="🎬 Movie Casting Manager — Next Gen", layout="wide")

# Simple CSS tweaks for nicer cards
st.markdown(
    """
    <style>
    .card {
        background-color: #ffffff;
        border-radius: 14px;
        padding: 12px;
        box-shadow: 0 6px 18px rgba(17,24,39,0.06);
        transition: transform .12s ease, box-shadow .12s ease;
        height: 290px; /* fixed height for grid alignment */
        overflow: hidden;
    }
    .card:hover { transform: translateY(-6px); box-shadow: 0 12px 30px rgba(17,24,39,0.09); }
    .avatar { border-radius: 12px; }
    .small-chip { display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; margin-right:6px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# Persistence: save / load
# -------------------------
def save_projects_to_disk(projects: dict):
    serializable = {}
    for proj, parts in projects.items():
        serializable[proj] = []
        for p in parts:
            p_copy = p.copy()
            # we store binary images as base64 strings for persistence
            if p_copy.get("photo_bytes"):
                p_copy["photo_b64"] = base64.b64encode(p_copy["photo_bytes"]).decode()
                p_copy.pop("photo_bytes", None)
            serializable[proj].append(p_copy)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def load_projects_from_disk():
    if not os.path.exists(DATA_FILE):
        return {"Default Project": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    projects = {}
    for proj, parts in raw.items():
        projects[proj] = []
        for p in parts:
            p_copy = p.copy()
            if p_copy.get("photo_b64"):
                p_copy["photo_bytes"] = base64.b64decode(p_copy.pop("photo_b64"))
            projects[proj].append(p_copy)
    return projects

# -------------------------
# Utility functions
# -------------------------
def role_color(role_text):
    # consistent hash-based pastel color
    if not role_text:
        role_text = "default"
    h = hashlib.md5(role_text.encode()).hexdigest()
    r = (int(h[:2], 16) + 200) // 2
    g = (int(h[2:4], 16) + 200) // 2
    b = (int(h[4:6], 16) + 200) // 2
    return f"#{r:02X}{g:02X}{b:02X}"

def next_free_number(participants):
    used = {p["number"] for p in participants}
    n = 1
    while n in used:
        n += 1
    return n

def image_bytes_to_stream(img_bytes, size=(300, 300), rounded=True):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail(size)
        if rounded:
            img = ImageOps.fit(img, (size[0], size[1]))
            mask = Image.new("L", img.size, 0)
            draw = Image.new("L", img.size, 0)
            # use rounded corners
            corner = Image.new("L", (size[0]//6, size[1]//6), 0)
            mask = Image.new("L", img.size, 255)
            out = io.BytesIO()
            img.save(out, format="JPEG")
            out.seek(0)
            return out
        else:
            out = io.BytesIO()
            img.save(out, format="JPEG")
            out.seek(0)
            return out
    except Exception:
        return None

def make_word_doc(participants, project_name="Project"):
    doc = Document()
    doc.add_heading(f"Participants - {project_name}", 0)
    # sort by number for nicer ordering in doc
    for p in sorted(participants, key=lambda x: x["number"]):
        table = doc.add_table(rows=1, cols=2)
        table.autofit = False
        table.columns[0].width = Inches(1.6)
        table.columns[1].width = Inches(4.8)
        cells = table.rows[0].cells
        if p.get("photo_bytes"):
            try:
                img_stream = io.BytesIO(p["photo_bytes"])
                paragraph = cells[0].paragraphs[0]
                run = paragraph.add_run()
                run.add_picture(img_stream, width=Inches(1.3))
            except Exception:
                cells[0].text = "No Photo"
        else:
            cells[0].text = "No Photo"

        info = (
            f"#{p['number']} - {p.get('name','Unnamed')}\n"
            f"Role: {p.get('role','')}\n"
            f"Age: {p.get('age','')}\n"
            f"Agency: {p.get('agency','')}\n"
            f"Height: {p.get('height','')}\n"
            f"Waist: {p.get('waist','')}\n"
            f"Dress/Suit: {p.get('dress_suit','')}"
        )
        cells[1].text = info
        doc.add_paragraph("")
    return doc

# -------------------------
# Initialize session state
# -------------------------
if "projects_loaded" not in st.session_state:
    st.session_state["projects"] = load_projects_from_disk()
    # ensure at least one project exists
    if not st.session_state["projects"]:
        st.session_state["projects"] = {"Default Project": []}
    st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
    st.session_state["projects_loaded"] = True
    st.session_state["selected_for_batch"] = set()

# -------------------------
# Sidebar (controls & form)
# -------------------------
sidebar = st.sidebar
sidebar.title("📂 Projects")
proj_names = list(st.session_state["projects"].keys())
current = sidebar.selectbox("Select project", proj_names, index=proj_names.index(st.session_state["current_project"]))
st.session_state["current_project"] = current

with sidebar.expander("➕ Create Project", expanded=False):
    new_proj = st.text_input("New project name", key="new_proj_name")
    if st.button("Create project"):
        if new_proj:
            if new_proj in st.session_state["projects"]:
                st.warning("Project already exists.")
            else:
                st.session_state["projects"][new_proj] = []
                st.session_state["current_project"] = new_proj
                save_projects_to_disk(st.session_state["projects"])
                st.success(f"Project '{new_proj}' created.")
                st.experimental_rerun()

with sidebar.expander("⚙️ Manage Projects", expanded=False):
    rename_to = st.text_input("Rename current project", value=current, key="rename_proj")
    if st.button("Rename"):
        if rename_to and rename_to not in st.session_state["projects"]:
            st.session_state["projects"][rename_to] = st.session_state["projects"].pop(current)
            st.session_state["current_project"] = rename_to
            save_projects_to_disk(st.session_state["projects"])
            st.success("Renamed.")
            st.experimental_rerun()
    if st.button("🗑 Delete current project"):
        if len(st.session_state["projects"]) <= 1:
            st.error("Need at least one project.")
        else:
            st.session_state["projects"].pop(current)
            st.session_state["current_project"] = list(st.session_state["projects"].keys())[0]
            save_projects_to_disk(st.session_state["projects"])
            st.success("Deleted project.")
            st.experimental_rerun()

sidebar.markdown("---")
sidebar.subheader("➕ Add participant")
with sidebar.form("add_participant"):
    name = st.text_input("Name")
    age = st.text_input("Age")
    agency = st.text_input("Agency")
    height = st.text_input("Height")
    waist = st.text_input("Waist")
    dress_suit = st.text_input("Dress/Suit Size")
    role = st.text_input("Role/Status")
    photo = st.file_uploader("Picture (png/jpg)", type=["png", "jpg", "jpeg"])
    submitted = st.form_submit_button("Add participant")
    if submitted:
        participants = st.session_state["projects"][st.session_state["current_project"]]
        number = next_free_number(participants)
        photo_bytes = None
        if photo:
            photo_bytes = photo.read()
        entry = {
            "number": number,
            "name": name,
            "age": age,
            "agency": agency,
            "height": height,
            "waist": waist,
            "dress_suit": dress_suit,
            "role": role,
            "photo_bytes": photo_bytes,
        }
        participants.append(entry)
        save_projects_to_disk(st.session_state["projects"])
        st.success(f"Added #{number} — {name}")
        st.experimental_rerun()

sidebar.markdown("---")
# Export and persistence
if st.button("📥 Export project (Word)"):
    parts = st.session_state["projects"][st.session_state["current_project"]]
    if not parts:
        st.info("No participants to export.")
    else:
        doc = make_word_doc(parts, st.session_state["current_project"])
        bio = io.BytesIO()
        doc.save(bio)
        bio.seek(0)
        st.download_button(
            "Download Word file",
            data=bio,
            file_name=f"{st.session_state['current_project']}_participants.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

if st.button("💾 Download project JSON"):
    bio = io.BytesIO()
    save_projects_to_disk(st.session_state["projects"])  # ensure file exists
    with open(DATA_FILE, "rb") as f:
        bio.write(f.read())
    bio.seek(0)
    st.download_button("Download JSON", data=bio, file_name=f"{st.session_state['current_project']}.json", mime="application/json")

# -------------------------
# Main area: grid, batch actions, and cards
# -------------------------
st.title(f"🎬 {st.session_state['current_project']}")
parts = st.session_state["projects"][st.session_state["current_project"]]

# Batch actions header
col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    select_all = st.checkbox("Select all", value=False, key=f"select_all_{current}")
    if select_all:
        st.session_state["selected_for_batch"] = {p["number"] for p in parts}
    else:
        # keep existing - user can uncheck individually on cards
        if "selected_for_batch" not in st.session_state:
            st.session_state["selected_for_batch"] = set()
with col2:
    st.write("")  # spacer
with col3:
    if st.button("🗑 Delete selected"):
        if not st.session_state["selected_for_batch"]:
            st.info("No participants selected.")
        else:
            before = len(parts)
            parts[:] = [p for p in parts if p["number"] not in st.session_state["selected_for_batch"]]
            st.session_state["selected_for_batch"] = set()
            save_projects_to_disk(st.session_state["projects"])
            st.success(f"Deleted {before - len(parts)} participant(s).")
            st.experimental_rerun()

# Grid display: 3 columns
if not parts:
    st.info("No participants yet. Add some from the sidebar.")
else:
    columns = st.columns(3, gap="large")
    for i, p in enumerate(parts):
        col = columns[i % 3]
        with col:
            # Card container
            st.markdown('<div class="card">', unsafe_allow_html=True)

            # Top row: number, name, role chip
            header_cols = st.columns([3, 1])
            with header_cols[0]:
                st.markdown(f"### #{p['number']} — {p.get('name','Unnamed')}")
            with header_cols[1]:
                c = role_color(p.get("role",""))
                st.markdown(f"<div class='small-chip' style='background:{c}; color:#fff'>{p.get('role','')}</div>", unsafe_allow_html=True)

            # Avatar
            if p.get("photo_bytes"):
                img_stream = image_bytes_to_stream(p["photo_bytes"], size=(180, 180))
                if img_stream:
                    st.image(img_stream, width=160, caption=None)
            else:
                st.image(Image.new("RGB", (180, 180), (240,240,240)), width=160)

            # Key stats as chips
            stats = []
            if p.get("age"): stats.append(f"Age: {p['age']}")
            if p.get("height"): stats.append(f"Height: {p['height']}")
            if p.get("agency"): stats.append(f"Agency: {p['agency']}")
            st.markdown("<br/>".join([f"<span class='small-chip' style='background:#F3F4F6'>{s}</span>" for s in stats]), unsafe_allow_html=True)

            st.write("")  # spacer

            # Inline (collapsible) edit area
            with st.expander("✏️ Edit participant", expanded=False):
                new_name = st.text_input("Name", value=p.get("name",""), key=f"name_{current}_{i}")
                new_age = st.text_input("Age", value=p.get("age",""), key=f"age_{current}_{i}")
                new_agency = st.text_input("Agency", value=p.get("agency",""), key=f"agency_{current}_{i}")
                new_height = st.text_input("Height", value=p.get("height",""), key=f"height_{current}_{i}")
                new_waist = st.text_input("Waist", value=p.get("waist",""), key=f"waist_{current}_{i}")
                new_ds = st.text_input("Dress/Suit", value=p.get("dress_suit",""), key=f"ds_{current}_{i}")
                new_role = st.text_input("Role/Status", value=p.get("role",""), key=f"role_{current}_{i}")
                uploaded = st.file_uploader("Replace picture (optional)", type=["png","jpg","jpeg"], key=f"up_{current}_{i}")

                # Number edit with suggestion + uniqueness check
                suggested = next_free_number(parts)
                new_num = st.number_input("Number (ID)", min_value=1, step=1, value=p["number"], key=f"numedit_{current}_{i}")
                if new_num != p["number"]:
                    if st.button("Save new number", key=f"save_num_btn_{current}_{i}"):
                        others = [x["number"] for x in parts if x is not p]
                        if new_num in others:
                            st.error(f"⚠️ #{new_num} already used. Next free: #{suggested}")
                        else:
                            p["number"] = new_num
                            save_projects_to_disk(st.session_state["projects"])
                            st.success("Number updated.")
                            st.experimental_rerun()

                if st.button("Save changes", key=f"save_changes_{current}_{i}"):
                    p["name"] = new_name
                    p["age"] = new_age
                    p["agency"] = new_agency
                    p["height"] = new_height
                    p["waist"] = new_waist
                    p["dress_suit"] = new_ds
                    p["role"] = new_role
                    if uploaded:
                        p["photo_bytes"] = uploaded.read()
                    save_projects_to_disk(st.session_state["projects"])
                    st.success("Participant updated.")
                    st.experimental_rerun()

            # Action row: select / export single / delete
            action_cols = st.columns([1,1,1])
            with action_cols[0]:
                checked = st.checkbox("Select", value=(p["number"] in st.session_state.get("selected_for_batch", set())), key=f"chk_{current}_{i}")
                # sync selection set
                if checked:
                    st.session_state["selected_for_batch"].add(p["number"])
                else:
                    st.session_state["selected_for_batch"].discard(p["number"])

            with action_cols[1]:
                if st.button("⬇ Export", key=f"export_{current}_{i}"):
                    doc = make_word_doc([p], st.session_state["current_project"])
                    bio = io.BytesIO()
                    doc.save(bio)
                    bio.seek(0)
                    st.download_button(
                        "Download participant",
                        data=bio,
                        file_name=f"{st.session_state['current_project']}_{p['number']}_{p.get('name','')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )

            with action_cols[2]:
                if st.button("🗑 Delete", key=f"del_{current}_{i}"):
                    parts.remove(p)
                    save_projects_to_disk(st.session_state["projects"])
                    st.success("Deleted.")
                    st.experimental_rerun()

            st.markdown("</div>", unsafe_allow_html=True)  # close card

# -------------------------
# Footer helpers
# -------------------------
st.markdown("---")
st.caption("Built for quick scanning, inline edits, batch operations, and persistent storage. Tell me what else you'd like (filters, drag/drop reorder, or CSV import) and I'll add it.")

