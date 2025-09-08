import streamlit as st
from PIL import Image
import io
from docx import Document
from docx.shared import Inches
import hashlib
import json
import os
import time
from datetime import datetime

# -------------------------
# Config
# -------------------------
DATA_FILE = "projects_data.json"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

st.set_page_config(page_title="🎬 Movie Casting Manager", layout="wide")

# -------------------------
# Persistence helpers
# -------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"Default Project": []}
    return {"Default Project": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# -------------------------
# Utilities
# -------------------------
def role_color(role_text):
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

def save_uploaded_image(uploaded_file, project_name, number):
    """Save uploaded file to UPLOAD_DIR and return filename (relative)."""
    if not uploaded_file:
        return None
    raw = uploaded_file.read()
    # create safe filename
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_project = "".join(c if c.isalnum() else "_" for c in project_name)[:40]
    ext = os.path.splitext(uploaded_file.name)[1].lower() or ".jpg"
    filename = f"{safe_project}_#{number}_{ts}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(raw)
    return filename

def read_image_bytes(filename):
    if not filename:
        return None
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()

def make_word_doc(participants, project_name="Project"):
    doc = Document()
    doc.add_heading(f"Participants - {project_name}", 0)
    # sort by number
    for p in sorted(participants, key=lambda x: x["number"]):
        table = doc.add_table(rows=1, cols=2)
        table.autofit = False
        table.columns[0].width = Inches(1.6)
        table.columns[1].width = Inches(4.8)
        cells = table.rows[0].cells

        if p.get("photo_filename"):
            img_bytes = read_image_bytes(p["photo_filename"])
            if img_bytes:
                try:
                    img_stream = io.BytesIO(img_bytes)
                    paragraph = cells[0].paragraphs[0]
                    run = paragraph.add_run()
                    run.add_picture(img_stream, width=Inches(1.3))
                except Exception:
                    cells[0].text = "No Photo"
            else:
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
# Session initialization
# -------------------------
if "projects" not in st.session_state:
    st.session_state["projects"] = load_data()
if "current_project" not in st.session_state:
    st.session_state["current_project"] = list(st.session_state["projects"].keys())[0] \
        if st.session_state["projects"] else "Default Project"
if "selected_for_batch" not in st.session_state:
    st.session_state["selected_for_batch"] = set()

# -------------------------
# CSS / small style
# -------------------------
st.markdown("""
<style>
body { font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F8F9FA; }
.card { background: #fff; border-radius:12px; padding:12px; box-shadow:0 8px 20px rgba(17,24,39,0.06); margin-bottom:12px; }
.card:hover { transform: translateY(-6px); box-shadow:0 12px 30px rgba(17,24,39,0.09); }
.small-chip { display:inline-block; padding:6px 10px; border-radius:999px; font-size:12px; margin-right:6px; background:#F3F4F6; }
</style>
""", unsafe_allow_html=True)

# -------------------------
# Sidebar: projects + add form
# -------------------------
sidebar = st.sidebar
sidebar.title("📂 Projects")
projects = st.session_state["projects"]

proj_names = list(projects.keys())
# ensure at least one project exists
if not proj_names:
    projects["Default Project"] = []
    save_data(projects)
    proj_names = list(projects.keys())

current = sidebar.selectbox("Select project", proj_names, index=proj_names.index(st.session_state["current_project"]))
st.session_state["current_project"] = current

with sidebar.expander("➕ Create Project"):
    new_name = st.text_input("Project name", key="new_project_name")
    if st.button("Create project"):
        if not new_name:
            sidebar.warning("Type a project name.")
        elif new_name in projects:
            sidebar.warning("Project already exists.")
        else:
            projects[new_name] = []
            save_data(projects)
            st.session_state["current_project"] = new_name
            st.success(f"Created '{new_name}'")
            st.rerun()

with sidebar.expander("⚙️ Manage Projects"):
    rename_to = st.text_input("Rename current project", value=current, key="rename_to")
    if st.button("Rename project"):
        if rename_to and rename_to not in projects:
            projects[rename_to] = projects.pop(current)
            st.session_state["current_project"] = rename_to
            save_data(projects)
            st.success("Renamed.")
            st.rerun()
    if st.button("🗑 Delete current project"):
        if len(projects) <= 1:
            sidebar.error("At least one project must remain.")
        else:
            projects.pop(current)
            st.session_state["current_project"] = list(projects.keys())[0]
            save_data(projects)
            st.success("Deleted project.")
            st.rerun()

sidebar.markdown("---")
sidebar.subheader(f"➕ Add participant to '{st.session_state['current_project']}'")
with sidebar.form("add_participant_form"):
    name = st.text_input("Name")
    age = st.text_input("Age")
    agency = st.text_input("Agency")
    height = st.text_input("Height")
    waist = st.text_input("Waist")
    dress_suit = st.text_input("Dress/Suit")
    role = st.text_input("Role/Status")
    photo = st.file_uploader("Photo (optional)", type=["png","jpg","jpeg"])
    submit = st.form_submit_button("Add participant")
    if submit:
        parts = projects[st.session_state["current_project"]]
        number = next_free_number(parts)
        photo_filename = save_uploaded_image(photo, st.session_state["current_project"], number) if photo else None
        entry = {
            "number": number,
            "name": name,
            "age": age,
            "agency": agency,
            "height": height,
            "waist": waist,
            "dress_suit": dress_suit,
            "role": role,
            "photo_filename": photo_filename,
            "added_at": time.time()
        }
        parts.append(entry)
        save_data(projects)
        st.success(f"Added #{number} — {name or 'Unnamed'}")
        st.rerun()

sidebar.markdown("---")
if sidebar.button("💾 Download project JSON"):
    proj_data = projects[st.session_state["current_project"]]
    bio = io.BytesIO()
    bio.write(json.dumps({st.session_state["current_project"]: proj_data}, ensure_ascii=False, indent=2).encode("utf-8"))
    bio.seek(0)
    sidebar.download_button("Download JSON", data=bio, file_name=f"{st.session_state['current_project']}.json", mime="application/json")

# -------------------------
# Main area: search, batch actions, grid
# -------------------------
st.title(f"🎬 {st.session_state['current_project']}")

# Search / filters
search = st.text_input("🔍 Search by name or role (case-insensitive)")
filter_role = st.text_input("Filter by role (exact text)", max_chars=50)
parts_all = projects[st.session_state["current_project"]]

# copy list for display and sorting
parts_display = sorted(parts_all, key=lambda x: x["number"])

# Apply search & filter
if search:
    q = search.lower()
    parts_display = [p for p in parts_display if q in (p.get("name","") or "").lower() or q in (p.get("role","") or "").lower()]
if filter_role:
    fr = filter_role.strip().lower()
    parts_display = [p for p in parts_display if (p.get("role","") or "").strip().lower() == fr]

# Batch controls
col1, col2, col3 = st.columns([1,2,1])
with col1:
    select_all = st.checkbox("Select all visible", value=False, key=f"select_all_{st.session_state['current_project']}")
    if select_all:
        st.session_state["selected_for_batch"] = {p["number"] for p in parts_display}
    else:
        # if unchecking, clear selection for visible ones
        visible = {p["number"] for p in parts_display}
        st.session_state["selected_for_batch"] = set(filter(lambda n: n not in visible, st.session_state.get("selected_for_batch", set())))
with col3:
    if st.button("🗑 Delete selected"):
        to_delete = st.session_state.get("selected_for_batch", set())
        if not to_delete:
            st.info("No participants selected.")
        else:
            before = len(parts_all)
            kept = []
            for p in parts_all:
                if p["number"] in to_delete:
                    # delete image file if exists
                    fn = p.get("photo_filename")
                    if fn:
                        path = os.path.join(UPLOAD_DIR, fn)
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                    continue
                kept.append(p)
            projects[st.session_state["current_project"]] = kept
            save_data(projects)
            st.session_state["selected_for_batch"] = set()
            st.success(f"Deleted {before - len(kept)} participant(s).")
            st.rerun()

# Grid
if not parts_display:
    st.info("No participants match your search/filter, or there are no participants yet.")
else:
    cols = st.columns(3, gap="large")
    for i, p in enumerate(parts_display):
        col = cols[i % 3]
        with col:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"### #{p['number']} — {p.get('name','Unnamed')}")
            chip_color = role_color(p.get("role",""))
            st.markdown(f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'><div style='background:{chip_color};padding:6px 10px;border-radius:999px;color:white'>{p.get('role','')}</div><div style='font-size:12px;color:#666'>Added: {datetime.fromtimestamp(p.get('added_at', time.time())).strftime('%Y-%m-%d')}</div></div>", unsafe_allow_html=True)

            # show photo (read file)
            img_bytes = read_image_bytes(p.get("photo_filename"))
            if img_bytes:
                try:
                    img = Image.open(io.BytesIO(img_bytes))
                    st.image(img, width=160)
                except Exception:
                    st.image(Image.new("RGB", (160,160), (240,240,240)), width=160)
            else:
                st.image(Image.new("RGB", (160,160), (240,240,240)), width=160)

            # quick stats
            stats = []
            if p.get("age"): stats.append(f"Age: {p.get('age')}")
            if p.get("height"): stats.append(f"Height: {p.get('height')}")
            if p.get("agency"): stats.append(f"Agency: {p.get('agency')}")
            st.markdown("<br/>".join([f"<span class='small-chip'>{s}</span>" for s in stats]), unsafe_allow_html=True)

            # Inline edit
            with st.expander("✏️ Edit", expanded=False):
                new_name = st.text_input("Name", value=p.get("name",""), key=f"name_{p['number']}")
                new_age = st.text_input("Age", value=p.get("age",""), key=f"age_{p['number']}")
                new_agency = st.text_input("Agency", value=p.get("agency",""), key=f"agency_{p['number']}")
                new_height = st.text_input("Height", value=p.get("height",""), key=f"height_{p['number']}")
                new_waist = st.text_input("Waist", value=p.get("waist",""), key=f"waist_{p['number']}")
                new_ds = st.text_input("Dress/Suit", value=p.get("dress_suit",""), key=f"ds_{p['number']}")
                new_role = st.text_input("Role/Status", value=p.get("role",""), key=f"role_{p['number']}")
                replace = st.file_uploader("Replace photo (optional)", type=["png","jpg","jpeg"], key=f"up_{p['number']}")
                suggested = next_free_number(parts_all)
                new_num = st.number_input("Number (ID)", min_value=1, step=1, value=p["number"], key=f"numedit_{p['number']}")
                if new_num != p["number"]:
                    if st.button("Save new number", key=f"save_num_{p['number']}"):
                        others = [x["number"] for x in parts_all if x is not p]
                        if new_num in others:
                            st.error(f"⚠️ Number {new_num} already used. Next free: {suggested}")
                        else:
                            # update number
                            for cand in parts_all:
                                if cand is p:
                                    cand["number"] = new_num
                                    break
                            save_data(projects)
                            st.success("Number updated.")
                            st.rerun()
                if st.button("Save changes", key=f"save_{p['number']}"):
                    for cand in parts_all:
                        if cand is p:
                            cand["name"] = new_name
                            cand["age"] = new_age
                            cand["agency"] = new_agency
                            cand["height"] = new_height
                            cand["waist"] = new_waist
                            cand["dress_suit"] = new_ds
                            cand["role"] = new_role
                            if replace:
                                # delete old file
                                old_fn = cand.get("photo_filename")
                                if old_fn:
                                    old_path = os.path.join(UPLOAD_DIR, old_fn)
                                    if os.path.exists(old_path):
                                        try:
                                            os.remove(old_path)
                                        except Exception:
                                            pass
                                new_fn = save_uploaded_image(replace, st.session_state["current_project"], cand["number"])
                                cand["photo_filename"] = new_fn
                            break
                    save_data(projects)
                    st.success("Saved.")
                    st.rerun()

            # Actions row
            a1, a2, a3 = st.columns([1,1,1])
            with a1:
                checked = st.checkbox("Select", value=(p["number"] in st.session_state.get("selected_for_batch", set())), key=f"chk_{p['number']}")
                if checked:
                    st.session_state["selected_for_batch"].add(p["number"])
                else:
                    st.session_state["selected_for_batch"].discard(p["number"])
            with a2:
                if st.button("⬇ Export", key=f"exp_{p['number']}"):
                    doc = make_word_doc([p], st.session_state["current_project"])
                    bio = io.BytesIO()
                    doc.save(bio)
                    bio.seek(0)
                    st.download_button("Download participant", data=bio, file_name=f"{st.session_state['current_project']}_{p['number']}_{(p.get('name') or '')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            with a3:
                if st.button("🗑 Delete", key=f"del_{p['number']}"):
                    # remove file
                    fn = p.get("photo_filename")
                    if fn:
                        path = os.path.join(UPLOAD_DIR, fn)
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                    # remove participant
                    projects[st.session_state["current_project"]] = [q for q in parts_all if q is not p]
                    save_data(projects)
                    st.success("Deleted participant.")
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)

# -------------------------
# Export full project
# -------------------------
st.markdown("---")
if st.button("⬇ Export full project (Word)"):
    cur = st.session_state["current_project"]
    parts = projects[cur]
    if not parts:
        st.info("No participants to export.")
    else:
        doc = make_word_doc(parts, cur)
        bio = io.BytesIO()
        doc.save(bio)
        bio.seek(0)
        st.download_button("Download project Word", data=bio, file_name=f"{cur}_participants.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

st.caption("Images stored in 'uploads/' and metadata stored in 'projects_data.json'. Want thumbnails or an uploads cleanup tool next?")

