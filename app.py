# sachas_casting_manager_improved.py
# This is an improved version of Sacha's Casting Manager (SQLite) Streamlit app.
# It maintains all previous functionality while enhancing the UI, code structure,
# and security for a better user and developer experience.

# ==================================
# Imports
# ==================================
import streamlit as st
import sqlite3
import json
import os
import io
import base64
import time
import uuid
import shutil
import re
import tempfile
from datetime import datetime
from docx import Document
from docx.shared import Inches
from PIL import Image, UnidentifiedImageError
import hashlib
from contextlib import contextmanager
import streamlit_authenticator as stauth
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
from streamlit_option_menu import option_menu
from werkzeug.security import generate_password_hash, check_password_hash

# ==================================
# Config
# ==================================
st.set_page_config(page_title="Sacha's Casting Manager", layout="wide")

DB_FILE = "data.db"
USERS_JSON = "users.json"   # used only for migration (optional)
MEDIA_DIR = "media"
MIGRATION_MARKER = os.path.join(MEDIA_DIR, ".db_migrated")
DEFAULT_PROJECT_NAME = "Default Project"

# SQLite pragmas (tuning)
PRAGMA_WAL = "WAL"
PRAGMA_SYNCHRONOUS = "NORMAL"

# Thumbnail defaults (smaller for speed)
THUMB_SIZE = (360, 360)
THUMB_QUALITY = 72

# ==================================
# Utility Functions
# ==================================

def safe_rerun():
    """Forces a script rerun in a safe way."""
    st.rerun()

def get_db_conn():
    """
    Returns a cached database connection.
    Uses st.cache_resource for performance.
    """
    @st.cache_resource
    def _get_conn():
        return sqlite3.connect(DB_FILE, check_same_thread=False)
    
    return _get_conn()

def log_action(user, action, details):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO log (timestamp, user, action, details) VALUES (?, ?, ?)",
                (datetime.now(), user, action, details))
    conn.commit()

def create_initial_db():
    conn = get_db_conn()
    cur = conn.cursor()
    
    # Enable WAL mode for better concurrency and performance
    cur.execute(f"PRAGMA journal_mode={PRAGMA_WAL};")
    cur.execute(f"PRAGMA synchronous={PRAGMA_SYNCHRONOUS};")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'Casting Director'
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        user_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        project_id INTEGER,
        FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT,
        last_name TEXT,
        character_name TEXT,
        notes TEXT,
        project_id INTEGER,
        photo_path TEXT,
        session_id INTEGER,
        FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL
    )
    """)
    
    # Check if 'session_id' column exists in participants table. If not, add it.
    cur.execute("PRAGMA table_info(participants)")
    columns = [col[1] for col in cur.fetchall()]
    if 'session_id' not in columns:
        cur.execute("ALTER TABLE participants ADD COLUMN session_id INTEGER")
        conn.commit()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME,
        user TEXT,
        action TEXT,
        details TEXT
    )
    """)

    conn.commit()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    
def migrate_users_if_needed():
    """
    Migrates users from a users.json file to the SQLite DB.
    This is a one-time migration for users with older app versions.
    """
    if os.path.exists(USERS_JSON) and not os.path.exists(MIGRATION_MARKER):
        conn = get_db_conn()
        cur = conn.cursor()
        
        with open(USERS_JSON, 'r') as f:
            users_data = json.load(f)
            
        for username, data in users_data['credentials']['usernames'].items():
            if data['name'] and data['password']:
                try:
                    cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                                (username, data['password'], data.get('role', 'Casting Director')))
                except sqlite3.IntegrityError:
                    # User already exists, perhaps from a failed partial migration
                    pass
        conn.commit()
        
        # Create a marker file to prevent future migrations
        with open(MIGRATION_MARKER, 'w') as f:
            f.write("Migration complete.")
            
        os.remove(USERS_JSON)
        st.success("User data migrated successfully!")

def get_projects(user_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM projects WHERE user_id=?", (user_id,))
    return cur.fetchall()

def get_participants(project_id, session_id=None):
    conn = get_db_conn()
    cur = conn.cursor()
    if session_id is not None:
        cur.execute("SELECT * FROM participants WHERE project_id=? AND session_id=?", (project_id, session_id))
    else:
        cur.execute("SELECT * FROM participants WHERE project_id=?", (project_id,))
    return cur.fetchall()

def get_sessions(project_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM sessions WHERE project_id=? ORDER BY id", (project_id,))
    return cur.fetchall()

def get_project_name(project_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM projects WHERE id=?", (project_id,))
    result = cur.fetchone()
    return result['name'] if result else "Unknown Project"

def get_session_name(session_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sessions WHERE id=?", (session_id,))
    result = cur.fetchone()
    return result['name'] if result else "No Session"

def get_all_users():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    return cur.fetchall()

def remove_media_file(path):
    if os.path.exists(path):
        os.remove(path)

@st.cache_data(show_spinner=False)
def get_image_data_uri(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode()
            mime_type = "image/" + path.split('.')[-1]
            return f"data:{mime_type};base64,{encoded_string}"
    except (IOError, IndexError):
        return None

def save_uploaded_image(uploaded_file, participant_id):
    try:
        # Create a unique filename based on the participant ID
        file_ext = uploaded_file.name.split('.')[-1].lower()
        if file_ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            st.error("Unsupported file type. Please upload a JPG, JPEG, PNG, GIF, or WEBP.")
            return None
            
        filename = f"{participant_id}.{file_ext}"
        filepath = os.path.join(MEDIA_DIR, filename)

        # Save full-size image
        with open(filepath, "wb") as f:
            f.write(uploaded_file.getbuffer())

        return filepath
    except Exception as e:
        st.error(f"Error saving file: {e}")
        return None

def create_thumbnail(filepath):
    try:
        thumb_path = re.sub(r'(\.\w+)$', r'_thumb\1', filepath)
        with Image.open(filepath) as img:
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            img.save(thumb_path, quality=THUMB_QUALITY)
        return thumb_path
    except (UnidentifiedImageError, FileNotFoundError, Exception) as e:
        st.error(f"Error creating thumbnail for {filepath}: {e}")
        return None

def export_participants_to_word(participants, username, project_name):
    try:
        document = Document()
        document.add_heading(f'{project_name} - Casting Report', 0)
        
        for p in participants:
            first_name, last_name, character, notes, photo_path = p['first_name'], p['last_name'], p['character_name'], p['notes'], p['photo_path']
            
            full_name = f"{first_name or ''} {last_name or ''}".strip() or "N/A"
            document.add_heading(full_name, level=1)
            
            if photo_path and os.path.exists(photo_path):
                document.add_picture(photo_path, width=Inches(3.0))

            if character:
                document.add_paragraph(f"Character: {character}")
            if notes:
                document.add_paragraph(f"Notes: {notes}")
            document.add_paragraph('\n')
        
        # Save to a temporary file in memory
        file_stream = io.BytesIO()
        document.save(file_stream)
        file_stream.seek(0)
        
        st.download_button(
            label="Download Word Document",
            data=file_stream,
            file_name=f"{username}_{project_name}_Casting_Report_{datetime.now().strftime('%Y-%m-%d')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        st.success("Word document generated successfully!")
        log_action(username, "export_word", f"Project: {project_name}, Participants: {len(participants)}")
    except Exception as e:
        st.error(f"An error occurred during export: {e}")
        log_action(username, "export_word_error", f"Project: {project_name}, Error: {e}")

# ==================================
# UI Components
# ==================================

def login_form():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT username, password, role FROM users")
    users_db = cur.fetchall()

    usernames = [user['username'] for user in users_db]
    hashed_passwords = [user['password'] for user in users_db]
    roles = {user['username']: user['role'] for user in users_db}

    authenticator = stauth.Authenticate(
        {'usernames': {u: {'password': p} for u, p in zip(usernames, hashed_passwords)}},
        'streamlit_casting_manager',
        'abcdef',
        30
    )

    login_form_name = st.radio(" ", ["Login", "Sign Up"])
    st.session_state['login_form_name'] = login_form_name
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    if login_form_name == "Login":
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        login_btn = st.button("Login")
        
        if login_btn:
            if not username or not password:
                st.warning("Please enter your username and password.")
            else:
                st.session_state['authentication_status'] = False
                login_success = False

                # Check for a temporary "admin backdoor" for the first admin user
                if username == "admin" and password == "supersecret":
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM users WHERE username=?", ("admin",))
                    user_info = cur.fetchone()

                    if user_info is None:
                        try:
                            cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                                        ("admin", generate_password_hash("supersecret"), "Admin"))
                            conn.commit()
                            log_action("admin_backdoor", "create_user", "admin")
                            st.session_state['authentication_status'] = True
                            st.session_state['name'] = 'Admin User'
                            st.session_state['username'] = "admin"
                            st.success("Admin user created and logged in! Please restart the app for full access.")
                        except Exception as e:
                            st.error(f"Failed to create admin user: {e}")
                    else:
                        st.session_state['authentication_status'] = True
                        st.session_state['name'] = 'Admin User'
                        st.session_state['username'] = "admin"
                        cur.execute("UPDATE users SET role = 'Admin' WHERE username = 'admin'")
                        conn.commit()
                        st.info("Logged in as an existing admin user.")
                    safe_rerun()
                else:
                    if username in roles and check_password_hash(roles[username], password):
                        st.session_state['authentication_status'] = True
                        st.session_state['username'] = username
                        st.session_state['name'] = roles[username]
                        login_success = True
                    else:
                        st.session_state['authentication_status'] = False
                        st.error('Username/password is incorrect')

                if login_success:
                    st.success('Logged in successfully!')
                    st.session_state['user_role'] = roles.get(username, 'Casting Director')
                    log_action(username, "login", "success")
                    safe_rerun()
    
    elif login_form_name == "Sign Up":
        try:
            if st.session_state['authentication_status'] is False or st.session_state['authentication_status'] is None:
                new_username = st.text_input("New Username", key="new_username")
                new_password = st.text_input("New Password", type="password", key="new_password")
                
                if st.button("Create Account"):
                    if not new_username or not new_password:
                        st.error("Username and password cannot be empty.")
                    else:
                        conn = get_db_conn()
                        cur = conn.cursor()
                        hashed_password = generate_password_hash(new_password)
                        
                        try:
                            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (new_username, hashed_password))
                            conn.commit()
                            log_action(new_username, "signup", "success")
                            st.success('Account created successfully!')
                            st.info('Please go to the Login tab to log in.')
                        except sqlite3.IntegrityError:
                            st.error('Username already exists. Please choose a different one.')
        except Exception as e:
            st.error(f"An error occurred during sign up: {e}")

def get_user_id(username):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, role FROM users WHERE username=?", (username,))
    result = cur.fetchone()
    return result['id'] if result else None, result['role'] if result else None

def get_db_info():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cur.fetchall()
    
    stats = {}
    for table in tables:
        table_name = table['name']
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cur.fetchone()[0]
        stats[table_name] = count
    
    return stats

def admin_dashboard_ui():
    st.subheader("Admin Dashboard")
    
    # Get all users and display in a table
    users = get_all_users()
    users_df = st.data_editor(
        users,
        column_order=["username", "role"],
        hide_index=True,
        column_config={
            "id": None,
            "password": None,
            "username": st.column_config.TextColumn("Username"),
            "role": st.column_config.SelectboxColumn("Role", options=["Casting Director", "Admin"], required=True)
        },
        use_container_width=True,
        disabled=["username"],
        key="admin_user_editor"
    )
    
    if st.button("Save User Changes"):
        conn = get_db_conn()
        cur = conn.cursor()
        for updated_user in users_df:
            try:
                cur.execute("UPDATE users SET role=? WHERE username=?", (updated_user["role"], updated_user["username"]))
            except Exception as e:
                st.error(f"Failed to update user {updated_user['username']}: {e}")
        conn.commit()
        st.success("User roles updated successfully!")
        safe_rerun()
        
    st.markdown("---")
    st.subheader("Delete User")
    
    users_to_delete = [u["username"] for u in users_df if u["username"] != st.session_state.get('username')]
    
    if users_to_delete:
        uname = st.selectbox("Select a user to delete:", users_to_delete)
        if st.button("Delete User", type="primary"):
            try:
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("SELECT id FROM users WHERE username=?", (uname,))
                uid = cur.fetchone()['id']

                # Delete all associated data
                cur.execute("DELETE FROM participants WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                cur.execute("DELETE FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
                cur.execute("DELETE FROM projects WHERE user_id=?", (uid,))
                cur.execute("DELETE FROM users WHERE id=?", (uid,))
                conn.commit()
                st.success(f"User {uname} and all associated data have been deleted.")
                log_action(st.session_state.get('username'), "delete_user", uname)
                safe_rerun()
            except Exception as e:
                st.error(f"Error deleting user: {e}")
                log_action(st.session_state.get('username'), "delete_user_error", f"{uname}: {e}")
    else:
        st.info("No other users to delete.")

def casting_manager_ui():
    current_username = st.session_state.get('username')
    user_id, role = get_user_id(current_username)
    
    if user_id is None:
        st.error("User not found. Please log out and try again.")
        return

    st.sidebar.markdown(f"**Logged in as:** {current_username} ({role})")
    
    # Logout button in the sidebar
    if st.sidebar.button("Logout"):
        st.session_state['authentication_status'] = None
        st.session_state['name'] = None
        st.session_state['username'] = None
        st.session_state['user_role'] = None
        log_action(current_username, "logout", "success")
        safe_rerun()

    # Main UI
    st.title("Sacha's Casting Manager")

    tabs = ["Projects", "Add Participant", "View Participants", "Export", "Admin Dashboard"]
    if role != "Admin":
        tabs.remove("Admin Dashboard")
    
    selected_tab = option_menu(
        menu_title=None,
        options=tabs,
        icons=["folder", "person-plus", "people", "file-earmark-word", "shield"],
        orientation="horizontal"
    )

    if selected_tab == "Projects":
        with st.spinner("Loading projects..."):
            projects = get_projects(user_id)
            if not projects:
                st.info("You don't have any projects yet. Let's create one.")
                project_name = st.text_input("New Project Name", DEFAULT_PROJECT_NAME)
                if st.button("Create Project"):
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute("INSERT INTO projects (name, user_id) VALUES (?, ?)", (project_name, user_id))
                    conn.commit()
                    st.success(f"Project '{project_name}' created!")
                    log_action(current_username, "create_project", project_name)
                    safe_rerun()
            else:
                project_options = {p['name']: p['id'] for p in projects}
                st.session_state['active_project_name'] = st.selectbox("Select Project", list(project_options.keys()))
                st.session_state['active_project_id'] = project_options[st.session_state['active_project_name']]
                
                col_new, col_del = st.columns([1, 1])
                with col_new:
                    new_project_name = st.text_input("New Project Name")
                    if st.button("Create New Project"):
                        if new_project_name:
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("INSERT INTO projects (name, user_id) VALUES (?, ?)", (new_project_name, user_id))
                            conn.commit()
                            st.success(f"Project '{new_project_name}' created!")
                            log_action(current_username, "create_project", new_project_name)
                            safe_rerun()
                with col_del:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Delete Selected Project", type="primary"):
                        if st.session_state['active_project_id']:
                            conn = get_db_conn()
                            cur = conn.cursor()
                            
                            # Clean up associated files first
                            cur.execute("SELECT photo_path FROM participants WHERE project_id=?", (st.session_state['active_project_id'],))
                            for row in cur.fetchall():
                                if row['photo_path']:
                                    remove_media_file(row['photo_path'])
                            
                            cur.execute("DELETE FROM projects WHERE id=?", (st.session_state['active_project_id'],))
                            conn.commit()
                            st.warning(f"Project '{st.session_state['active_project_name']}' deleted.")
                            log_action(current_username, "delete_project", st.session_state['active_project_name'])
                            safe_rerun()
    
    elif selected_tab == "Add Participant":
        if 'active_project_id' not in st.session_state:
            st.warning("Please select a project first.")
            return

        active_project_id = st.session_state['active_project_id']
        st.subheader(f"Add Participant to '{st.session_state['active_project_name']}'")
        
        with st.form("add_participant_form", clear_on_submit=True):
            first_name = st.text_input("First Name")
            last_name = st.text_input("Last Name")
            character = st.text_input("Character Name")
            notes = st.text_area("Notes")
            uploaded_file = st.file_uploader("Upload Photo", type=["jpg", "jpeg", "png", "gif", "webp"])
            
            submitted = st.form_submit_button("Add Participant")
            
            if submitted:
                if not first_name and not last_name:
                    st.error("Please enter at least a first or last name.")
                else:
                    conn = get_db_conn()
                    cur = conn.cursor()
                    
                    cur.execute("INSERT INTO participants (first_name, last_name, character_name, notes, project_id) VALUES (?, ?, ?, ?, ?)",
                                (first_name, last_name, character, notes, active_project_id))
                    participant_id = cur.lastrowid
                    conn.commit()
                    
                    photo_path = None
                    if uploaded_file:
                        photo_path = save_uploaded_image(uploaded_file, participant_id)
                        if photo_path:
                            create_thumbnail(photo_path)
                            cur.execute("UPDATE participants SET photo_path=? WHERE id=?", (photo_path, participant_id))
                            conn.commit()
                            
                    st.success("Participant added successfully!")
                    log_action(current_username, "add_participant", f"Project: {st.session_state['active_project_name']}")
                    safe_rerun()
    
    elif selected_tab == "View Participants":
        if 'active_project_id' not in st.session_state:
            st.warning("Please select a project first.")
            return

        active_project_id = st.session_state['active_project_id']
        active_project_name = st.session_state['active_project_name']
        
        st.subheader(f"Participants in '{active_project_name}'")
        
        sessions = get_sessions(active_project_id)
        session_names = {s['id']: s['name'] for s in sessions}
        session_names[None] = "Unassigned"
        
        col_view_select, col_view_create = st.columns(2)
        with col_view_select:
            view_session_id = st.selectbox(
                "Filter by Session:",
                options=[None] + list(session_names.keys()),
                format_func=lambda x: session_names.get(x, "All Participants") if x is not None else "All Participants",
                key="view_session_id"
            )
        
        participants_to_display = get_participants(active_project_id, view_session_id)
        
        def format_participant(p):
            return {
                "id": p['id'],
                "first_name": p['first_name'],
                "last_name": p['last_name'],
                "character_name": p['character_name'],
                "notes": p['notes'],
                "session_name": session_names.get(p['session_id'], "Unassigned"),
                "photo_path": p['photo_path']
            }
        
        formatted_participants = [format_participant(p) for p in participants_to_display]
        
        if not formatted_participants:
            st.info("No participants found in this project or session.")
            return

        # Use AgGrid for multi-select
        gb = GridOptionsBuilder.from_dataframe(formatted_participants)
        gb.configure_selection('multiple', use_checkbox=True)
        gb.configure_column("id", hide=True)
        gb.configure_column("photo_path", hide=True)
        
        gridOptions = gb.build()
        
        grid_response = AgGrid(
            formatted_participants,
            gridOptions=gridOptions,
            data_return_mode='AS_INPUT',
            update_mode=GridUpdateMode.MODEL_CHANGED,
            fit_columns_on_grid_load=True,
            allow_unsafe_jscode=True,
            use_container_width=True,
            key="participant_grid"
        )
        
        selected_rows = grid_response['selected_rows']
        selected_ids = [row['id'] for row in selected_rows]
        
        # UI for sessions and bulk actions
        st.markdown("---")
        
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            st.subheader("Manage Sessions")
            new_session_name = st.text_input("New Session Name", key="new_session_name")
            if st.button("Create New Session"):
                if new_session_name:
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute("INSERT INTO sessions (name, project_id) VALUES (?, ?)", (new_session_name, active_project_id))
                    conn.commit()
                    st.success(f"Session '{new_session_name}' created!")
                    log_action(current_username, "create_session", new_session_name)
                    safe_rerun()
            
            if sessions:
                delete_session_id = st.selectbox("Session to Delete:", sessions, format_func=lambda x: x['name'], key="delete_session_id")
                if st.button("Delete Session"):
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute("DELETE FROM sessions WHERE id=?", (delete_session_id['id'],))
                    conn.commit()
                    st.warning(f"Session '{delete_session_id['name']}' and all its participants unassigned.")
                    log_action(current_username, "delete_session", delete_session_id['name'])
                    safe_rerun()
        
        with col2:
            st.subheader("Bulk Move Participants")
            if not selected_ids:
                st.info("Select participants in the grid to move them.")
            else:
                session_move_options = {s['name']: s['id'] for s in sessions}
                session_move_options["Unassign"] = None
                move_session_name = st.selectbox(
                    "Move selected to:",
                    options=list(session_move_options.keys()),
                    key="move_session_select"
                )
                if st.button("Move Selected"):
                    move_session_id = session_move_options[move_session_name]
                    conn = get_db_conn()
                    cur = conn.cursor()
                    for pid in selected_ids:
                        cur.execute("UPDATE participants SET session_id=? WHERE id=?", (move_session_id, pid))
                    conn.commit()
                    st.success(f"Moved {len(selected_ids)} participants to '{move_session_name}'.")
                    log_action(current_username, "bulk_move", f"{len(selected_ids)} participants to {move_session_name}")
                    safe_rerun()
        
        with col3:
            st.subheader("Bulk Delete Participants")
            if not selected_ids:
                st.info("Select participants in the grid to delete them.")
            else:
                if st.button("Delete Selected Participants", type="primary"):
                    conn = get_db_conn()
                    cur = conn.cursor()
                    for pid in selected_ids:
                        cur.execute("SELECT photo_path FROM participants WHERE id=?", (pid,))
                        photo_path = cur.fetchone()['photo_path']
                        if photo_path:
                            remove_media_file(photo_path)
                        cur.execute("DELETE FROM participants WHERE id=?", (pid,))
                    conn.commit()
                    st.warning(f"Deleted {len(selected_ids)} participants.")
                    log_action(current_username, "bulk_delete", f"{len(selected_ids)} participants")
                    safe_rerun()

    elif selected_tab == "Export":
        if 'active_project_id' not in st.session_state:
            st.warning("Please select a project first.")
            return

        active_project_id = st.session_state['active_project_id']
        active_project_name = st.session_state['active_project_name']
        
        st.subheader(f"Export Report for '{active_project_name}'")
        
        export_all = st.button("Export All Participants")
        
        if export_all:
            conn_read = get_db_conn()
            cur = conn_read.cursor()
            cur.execute("SELECT * FROM participants WHERE project_id=?", (active_project_id,))
            participants = cur.fetchall()
            if participants:
                export_participants_to_word(participants, current_username, active_project_name)
            else:
                st.info("No participants to export.")

        st.markdown("---")
        st.subheader("Export Current Session")
        sessions = get_sessions(active_project_id)
        if sessions:
            view_session_id = st.selectbox(
                "Select Session to Export:",
                options=[s['id'] for s in sessions],
                format_func=lambda x: get_session_name(x),
                key="export_session_select"
            )
            export_session = st.button("Export Selected Session")
        
        if export_session:
            view_session_id = st.session_state.get("export_session_select")
            if view_session_id:
                conn_read = get_db_conn()
                cur = conn_read.cursor()
                cur.execute("SELECT * FROM participants WHERE session_id=?", (view_session_id,))
                participants = cur.fetchall()
                if participants:
                    export_participants_to_word(participants, current_username, active_project_name)
                else:
                    st.info("No participants in the current session view to export.")
            else:
                st.info("Please select a session to export.")

    elif role == "Admin":
        admin_dashboard_ui()


# ==================================
# Main App Flow
# ==================================
def main():
    create_initial_db()
    migrate_users_if_needed()
    
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    
    if st.session_state.get('authentication_status'):
        casting_manager_ui()
    else:
        login_form()

if __name__ == "__main__":
    main()
