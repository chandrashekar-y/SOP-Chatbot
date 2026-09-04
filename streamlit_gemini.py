"""
SOP Chatbot - Perfectly Centered Login with Title on Top
"""

import streamlit as st
import hashlib
import os
import glob
import json
import time
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Engine ───────────────────────────────────────────────────────────────────
from hybrid_sop_engine import HybridSOPEngine as _EngineClass

_ENGINE_LABEL = f"Hybrid · {os.getenv('OLLAMA_LLM_MODEL', 'qwen2.5:14b')} · Local"
print(f"[App] Engine: {_ENGINE_LABEL}")

# ── Reload flag path (written by sop_watcher.py after new SOPs are converted) ─
_RELOAD_FLAG = os.path.join(os.path.dirname(__file__), "sop_inbox", ".reload_flag")

# ── Active session tracking ───────────────────────────────────────────────────
_SESSIONS_FILE = os.path.join(os.path.dirname(__file__), ".active_sessions.json")
_SESSION_TTL   = 90   # seconds — a session is "active" if heartbeat < 90s ago


def _load_sessions() -> dict:
    try:
        with open(_SESSIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_sessions(data: dict):
    try:
        with open(_SESSIONS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def update_active_session():
    """Call on every page run to register/refresh this session's heartbeat."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    sid      = st.session_state.session_id
    username = st.session_state.get("username", "unknown")
    now      = time.time()
    sessions = _load_sessions()
    # Prune stale sessions
    sessions = {k: v for k, v in sessions.items() if now - v.get("ts", 0) < _SESSION_TTL}
    sessions[sid] = {"ts": now, "user": username}
    _save_sessions(sessions)
    return sessions


def count_active_users() -> tuple[int, list[str]]:
    """Return (count, [usernames]) of currently active sessions."""
    now      = time.time()
    sessions = _load_sessions()
    active   = [v for v in sessions.values() if now - v.get("ts", 0) < _SESSION_TTL]
    users    = sorted({v["user"] for v in active})
    return len(active), users


def check_reload_flag():
    """
    Called on every page load. If sop_watcher.py has dropped a .reload_flag,
    clear the cached engine so it reloads with the new SOPs on the next request.
    Zero-downtime: no container restart needed.
    """
    if os.path.exists(_RELOAD_FLAG):
        try:
            os.remove(_RELOAD_FLAG)
            _get_engine.clear()
            # Also clear session engine reference so this session gets fresh engine
            if "rag_engine" in st.session_state:
                del st.session_state["rag_engine"]
            print("[App] Reload flag detected — engine cache cleared, new SOPs loaded.")
        except Exception as e:
            print(f"[App] Warning: could not process reload flag: {e}")


@st.cache_resource(show_spinner="Loading SOP database…")
def _get_engine():
    """Load the engine once at server start — shared across ALL users and sessions."""
    return _EngineClass()

FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "feedback.jsonl")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="SOP Chatbot",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={}
)

# ══════════════════════════════════════════════════════════════════════════════
# FEEDBACK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def save_feedback(rating: str, query: str, answer: str, practice: str,
                  comment: str = "", username: str = ""):
    """Append a feedback entry to feedback.jsonl."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user":      username,
        "practice":  practice,
        "query":     query,
        "answer":    answer[:500],   # truncate long answers
        "rating":    rating,         # "up" or "down"
        "comment":   comment.strip(),
    }
    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        st.warning(f"Could not save feedback: {e}")


def load_feedback(limit: int = 50):
    """Load the most recent feedback entries."""
    if not os.path.exists(FEEDBACK_FILE):
        return []
    entries = []
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return entries[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
# USER DATABASE
# ══════════════════════════════════════════════════════════════════════════════

USERS = {
    "admin": {
        "password_hash": hashlib.sha256("admin123".encode()).hexdigest(),
        "role": "admin",
        "practices": ["*"]
    },
    "billing": {
        "password_hash": hashlib.sha256("billing123".encode()).hexdigest(),
        "role": "staff",
        "practices": ["*"]
    },
    "demo": {
        "password_hash": hashlib.sha256("demo123".encode()).hexdigest(),
        "role": "staff",
        "practices": ["*"]
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_credentials(username, password):
    if username in USERS:
        return hash_password(password) == USERS[username]["password_hash"], USERS[username]
    return False, None

# ══════════════════════════════════════════════════════════════════════════════
# CENTERED LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def show_login():
    """Themed login page — animated gradient + glassmorphism"""

    st.html("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Red+Hat+Display:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
        /* ── Hide sidebar ── */
        [data-testid='stSidebar'] { display: none !important; }

        /* ── Remove top black gap on login page ── */
        [data-testid="stHeader"],
        header[data-testid="stHeader"],
        .stApp > header { display: none !important; }
        [data-testid="stAppViewContainer"] > section.main > div.block-container {
            padding-top: 0 !important;
            margin-top: 0 !important;
        }
        [data-testid="stMain"] { padding-top: 0 !important; }

        /* ── Hide sidebar collapse button everywhere ── */
        section[data-testid="stSidebarCollapsedControl"],
        section[data-testid="collapsedControl"],
        [data-testid="stBaseButton-headerNoPadding"],
        button[aria-label="Collapse sidebar"],
        button[aria-label="Expand sidebar"] {
            display: none !important;
        }

        /* ── Animated gradient background ── */
        @keyframes gradientShift {
            0%   { background-position: 0% 50%; }
            50%  { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            background: linear-gradient(-45deg, #063e70, #0a5a9c, #08ad9b, #045e54, #063e70) !important;
            background-size: 400% 400% !important;
            animation: gradientShift 12s ease infinite !important;
            font-family: 'Inter', 'Open Sans', Arial, sans-serif !important;
        }

        /* ── Center container ── */
        .main .block-container {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding-top: 1rem !important;
            padding-bottom: 1rem !important;
        }

        /* ── Input fields — white on glass card ── */
        .stTextInput input,
        .stTextInput > div > div > input,
        input[type="text"],
        input[type="password"],
        [data-testid="stTextInput"] input {
            background-color: rgba(255,255,255,0.92) !important;
            color: #121c27 !important;
            border: 1px solid rgba(8,173,155,0.35) !important;
            border-radius: 8px !important;
            caret-color: #063e70 !important;
            font-family: 'Inter', sans-serif !important;
        }
        .stTextInput input:focus,
        [data-testid="stTextInput"] input:focus {
            background-color: #ffffff !important;
            border-color: #08ad9b !important;
            box-shadow: 0 0 0 2px rgba(8,173,155,0.25) !important;
        }
        .stTextInput input::placeholder { color: #7EBEC5 !important; }
        .stTextInput label {
            color: #063e70 !important;
            font-weight: 500 !important;
            font-size: 13px !important;
        }

        /* ── Login button ── */
        .stForm [data-testid="stFormSubmitButton"] button,
        .stFormSubmitButton button {
            background: linear-gradient(135deg, #08ad9b, #063e70) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 8px !important;
            font-size: 15px !important;
            font-weight: 600 !important;
            padding: 0.5rem 1rem !important;
            width: 100% !important;
            transition: opacity 0.2s !important;
            font-family: 'Inter', sans-serif !important;
            box-shadow: 0 4px 15px rgba(8,173,155,0.4) !important;
        }
        .stForm [data-testid="stFormSubmitButton"] button:hover,
        .stFormSubmitButton button:hover { opacity: 0.88 !important; }

        /* ── Expander ── */
        [data-testid="stExpander"] {
            border: 1px solid rgba(8,173,155,0.2) !important;
            border-radius: 8px !important;
            background: rgba(255,255,255,0.7) !important;
        }
        [data-testid="stExpander"] summary { color: #063e70 !important; font-size: 13px !important; }
        [data-testid="stAlert"][data-baseweb="notification"] { border-radius: 8px !important; }
        </style>
    """)

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        # Glassmorphism card with animated gradient header
        st.markdown(
            """
            <div style="background:rgba(255,255,255,0.88);backdrop-filter:blur(18px);
                        -webkit-backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,0.5);
                        border-radius:20px;overflow:hidden;width:100%;max-width:440px;
                        margin:0 auto;box-shadow:0 8px 40px rgba(6,62,112,0.25);">
              <!-- Header -->
              <div style="background:linear-gradient(135deg,#063e70 0%,#08ad9b 100%);
                          padding:22px 24px 18px;text-align:center;">
                <div style="font-family:'Red Hat Display','Inter',sans-serif;font-size:28px;
                            font-weight:800;letter-spacing:0.5px;line-height:1.2;">
                  <span style="color:#ffffff;">SOP</span><span style="color:#e53935;">hia</span>
                </div>
                <div style="font-size:11px;color:rgba(255,255,255,0.65);letter-spacing:1.5px;
                            text-transform:uppercase;margin-top:5px;font-family:'Inter',sans-serif;">
                  RCM &middot; Billing Operations
                </div>
              </div>
              <!-- Body -->
              <div style="padding:1.8rem 2rem 0.8rem;">
                <div style="width:40px;height:3px;
                            background:linear-gradient(90deg,#08ad9b,#063e70);
                            border-radius:2px;margin:0 auto 16px;"></div>
                <div style="text-align:center;font-size:17px;font-weight:600;color:#063e70;
                            margin-bottom:4px;font-family:'Red Hat Display',sans-serif;">
                  SOP Assistant
                </div>
                <div style="text-align:center;font-size:12px;color:#1467b3;margin-bottom:1.2rem;
                            font-family:'Inter',sans-serif;">
                  Sign in to continue
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Login form
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", placeholder="Enter your username")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submit = st.form_submit_button("Login", use_container_width=True)

            if submit:
                if not username or not password:
                        st.error("Please enter both username and password")
                else:
                    valid, user = verify_credentials(username, password)
                    if valid:
                        st.session_state.authenticated = True
                        st.session_state.username = username
                        st.session_state.role = user["role"]
                        st.session_state.practices = user["practices"]
                        st.session_state.login_time = datetime.now()
                        st.success("Login successful! Redirecting...")
                        st.rerun()
                    else:
                        st.error("Invalid username or password")

        # Demo credentials
        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("Demo Credentials"):
            st.code("Username: demo\nPassword: demo123")
            st.caption("Use these credentials to test the chatbot")

# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════

def initialize_session():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # feedback_state[idx] = "up" | "down" | None — tracks rating per message index
    if "feedback_state" not in st.session_state:
        st.session_state.feedback_state = {}

    # feedback_comment_open[idx] = True when thumbs-down comment box is open
    if "feedback_comment_open" not in st.session_state:
        st.session_state.feedback_comment_open = {}
    
    if "rag_engine" not in st.session_state:
        st.session_state.rag_engine = _get_engine()

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

def show_sidebar():

    st.html("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Red+Hat+Display:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
        /* ── Zero top gap — remove Streamlit default header padding ── */
        [data-testid="stAppViewContainer"] > section.main > div.block-container {
            padding-top: 0.5rem !important;
        }
        /* ── Remove the deploy/hamburger header bar entirely ── */
        [data-testid="stHeader"],
        header[data-testid="stHeader"],
        .stApp > header { display: none !important; }
        /* ── Hide sidebar collapse/expand toggle — all known selectors ── */
        section[data-testid="stSidebarCollapsedControl"],
        section[data-testid="collapsedControl"],
        button[data-testid="collapsedControl"],
        [data-testid="stBaseButton-headerNoPadding"],
        button[aria-label="Collapse sidebar"],
        button[aria-label="Expand sidebar"],
        button[title="Collapse sidebar"],
        button[title="Expand sidebar"],
        [data-testid="stSidebar"] > div > div > button:first-child {
            display: none !important;
        }

        /* ── Animated gradient — main chat area (light teal, distinct from sidebar) ── */
        @keyframes gradientShift {
            0%   { background-position: 0% 50%; }
            50%  { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            background: linear-gradient(-45deg, #0a5a6e, #0891b2, #08ad9b, #0d7a6e, #0a5a6e) !important;
            background-size: 400% 400% !important;
            animation: gradientShift 12s ease infinite !important;
            font-family: 'Inter', 'Open Sans', Arial, sans-serif !important;
        }
        /* ── Sidebar — navy (clearly darker than chat bg) ── */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #021d35 0%, #042f55 60%, #021d35 100%) !important;
            border-right: 1px solid rgba(8,173,155,0.25) !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            background: transparent !important;
        }
        [data-testid="stSidebar"] .stMarkdown p,
        [data-testid="stSidebar"] .stMarkdown div,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span { color: #ffffff !important; }
        [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12) !important; }
        /* ── Sidebar buttons — base style ── */
        [data-testid="stSidebar"] .stButton button {
            background: rgba(255,255,255,0.08) !important;
            color: #ffffff !important;
            border: 1px solid rgba(255,255,255,0.15) !important;
            border-radius: 8px !important;
            font-family: 'Inter', sans-serif !important;
            transition: all 0.25s ease !important;
        }
        [data-testid="stSidebar"] .stButton button:hover {
            background: linear-gradient(135deg,#08ad9b,#063e70) !important;
            border-color: rgba(8,173,155,0.6) !important;
            box-shadow: 0 0 14px rgba(8,173,155,0.55), 0 4px 12px rgba(8,173,155,0.3) !important;
        }
        /* ── Clear Chat — teal glow ── */
        [data-testid="stSidebar"] .stButton:nth-last-of-type(2) button {
            border-color: rgba(8,173,155,0.4) !important;
            box-shadow: 0 0 8px rgba(8,173,155,0.25) !important;
        }
        /* ── Logout — red glow ── */
        [data-testid="stSidebar"] .stButton:last-of-type button {
            border-color: rgba(229,57,53,0.35) !important;
            box-shadow: 0 0 8px rgba(229,57,53,0.2) !important;
        }
        [data-testid="stSidebar"] .stButton:last-of-type button:hover {
            background: linear-gradient(135deg,#e53935,#8b0000) !important;
            border-color: rgba(229,57,53,0.6) !important;
            box-shadow: 0 0 16px rgba(229,57,53,0.5), 0 4px 12px rgba(229,57,53,0.3) !important;
        }
        [data-testid="stSidebar"] .stSelectbox > div > div {
            background-color: rgba(255,255,255,0.10) !important;
            border: 1px solid rgba(255,255,255,0.18) !important;
            border-radius: 8px !important;
            color: #ffffff !important;
        }
        /* ── Chat messages — glassmorphism ── */
        [data-testid="stChatMessage"] {
            background: rgba(255,255,255,0.15) !important;
            backdrop-filter: blur(14px) !important;
            -webkit-backdrop-filter: blur(14px) !important;
            border: 1px solid rgba(255,255,255,0.3) !important;
            border-radius: 14px !important;
            box-shadow: 0 4px 16px rgba(6,62,112,0.1) !important;
            padding: 12px 16px !important;
            margin-bottom: 6px !important;
        }
        /* ── Assistant message — warmer glass ── */
        [data-testid="stChatMessage"]:nth-child(even) {
            background: rgba(255,255,255,0.82) !important;
            border: 1px solid rgba(8,173,155,0.25) !important;
            box-shadow: 0 4px 20px rgba(6,62,112,0.12) !important;
        }
        /* ── Hide avatar icons entirely ── */
        [data-testid="stChatMessage"] > div:first-child {
            display: none !important;
        }
        /* ── Expand message content to full width (no avatar gap) ── */
        [data-testid="stChatMessage"] > div:last-child {
            width: 100% !important;
            flex: 1 !important;
        }
        /* ── Chat message text — white on glass bubble ── */
        [data-testid="stChatMessage"] p,
        [data-testid="stChatMessage"] li,
        [data-testid="stChatMessage"] strong,
        [data-testid="stChatMessage"] em,
        [data-testid="stChatMessage"] code,
        [data-testid="stChatMessage"] .stMarkdown p,
        [data-testid="stChatMessage"] .stMarkdown li,
        [data-testid="stChatMessage"] .stMarkdown strong,
        [data-testid="stChatMessage"] .stMarkdown em {
            color: #ffffff !important;
        }
        /* ── Inline code in chat — strip grey pill completely ── */
        [data-testid="stChatMessage"] code,
        [data-testid="stChatMessage"] .stMarkdown code,
        [data-testid="stChatMessage"] .stMarkdown p code,
        [data-testid="stChatMessage"] .stMarkdown li code,
        [data-testid="stChatMessage"] .stMarkdown span code {
            background: transparent !important;
            background-color: transparent !important;
            border: none !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin: 0 !important;
            font-family: inherit !important;
            font-size: inherit !important;
            font-weight: inherit !important;
            color: #ffffff !important;
        }
        /* ── General main area text — white for teal gradient background ── */
        [data-testid="stMain"] .stMarkdown p,
        [data-testid="stMain"] .stMarkdown li,
        [data-testid="stMain"] .stMarkdown span {
            color: #ffffff !important;
        }
        /* ── Re-override: text inside chat bubbles white ── */
        [data-testid="stChatMessage"] .stMarkdown p,
        [data-testid="stChatMessage"] .stMarkdown li,
        [data-testid="stChatMessage"] .stMarkdown span {
            color: #ffffff !important;
        }
        /* ── ALL buttons in main area — fully transparent base ── */
        [data-testid="stMain"] .stButton > button,
        [data-testid="stMain"] .stButton > button:focus,
        [data-testid="stMain"] .stButton > button:active,
        [data-testid="stMain"] .stButton > button[kind="secondary"],
        [data-testid="stMain"] .stButton > button[kind="primary"] {
            background: transparent !important;
            background-color: transparent !important;
            background-image: none !important;
            border: 1px solid rgba(255,255,255,0.28) !important;
            box-shadow: none !important;
            border-radius: 8px !important;
            color: #ffffff !important;
            font-size: 15px !important;
            transition: all 0.2s ease !important;
        }
        [data-testid="stMain"] .stButton > button:hover {
            background: rgba(255,255,255,0.1) !important;
            border-color: rgba(255,255,255,0.55) !important;
            box-shadow: none !important;
        }
        /* ── Active thumbs-up — teal outline ── */
        [data-testid="stMain"] .stButton > button[kind="primary"] {
            border-color: rgba(8,173,155,0.8) !important;
            color: #08ad9b !important;
        }
        /* ── Submit feedback button — solid teal ── */
        [data-testid="stMain"] .stFormSubmitButton > button {
            background: linear-gradient(135deg, #08ad9b, #063e70) !important;
            background-color: #08ad9b !important;
            border: none !important;
            color: #ffffff !important;
            box-shadow: 0 0 10px rgba(8,173,155,0.35) !important;
        }
        [data-testid="stMain"] .stFormSubmitButton > button:hover {
            box-shadow: 0 0 18px rgba(8,173,155,0.6) !important;
        }
        /* ── Chat input container — glassmorphism ── */
        [data-testid="stBottom"],
        [data-testid="stBottom"] > div {
            background: transparent !important;
        }
        [data-testid="stBottom"] {
            padding-bottom: 0.5rem !important;
            padding-top: 0.25rem !important;
        }
        [data-testid="stChatInputContainer"],
        [data-testid="stChatInput"] > div {
            background: rgba(255,255,255,0.12) !important;
            backdrop-filter: blur(16px) !important;
            -webkit-backdrop-filter: blur(16px) !important;
            border: 1px solid rgba(255,255,255,0.25) !important;
            border-radius: 14px !important;
            box-shadow: 0 4px 20px rgba(6,62,112,0.2), 0 0 0 1px rgba(8,173,155,0.15) !important;
        }
        /* ── Textarea — fully transparent, no grey bg ── */
        [data-testid="stChatInput"] textarea,
        [data-testid="stChatInput"] textarea:focus,
        [data-testid="stChatInput"] textarea:active,
        [data-testid="stChatInput"] div[data-baseweb="textarea"],
        [data-testid="stChatInput"] div[data-baseweb="base-input"] {
            background: transparent !important;
            background-color: transparent !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: none !important;
            border-radius: 14px !important;
            font-family: 'Inter', sans-serif !important;
            caret-color: #08ad9b !important;
        }
        [data-testid="stChatInput"] textarea::placeholder {
            color: rgba(255,255,255,0.5) !important;
        }
        /* ── Send button glow ── */
        [data-testid="stChatInput"] button {
            background: linear-gradient(135deg,#08ad9b,#063e70) !important;
            border-radius: 10px !important;
            border: none !important;
            box-shadow: 0 0 10px rgba(8,173,155,0.4) !important;
            transition: all 0.2s !important;
        }
        [data-testid="stChatInput"] button:hover {
            box-shadow: 0 0 18px rgba(8,173,155,0.7) !important;
        }
        /* ── Feedback radio — compact inline pill style ── */
        [data-testid="stMain"] [data-testid="stRadio"] {
            margin: 2px 0 4px 0 !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] > label {
            font-size: 11px !important;
            color: rgba(255,255,255,0.55) !important;
            font-family: 'Inter', sans-serif !important;
            margin-bottom: 2px !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] [data-testid="stWidgetLabel"] p {
            font-size: 11px !important;
            color: rgba(255,255,255,0.55) !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] div[role="radiogroup"] {
            gap: 6px !important;
            flex-direction: row !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label[data-baseweb="radio"] {
            background: rgba(255,255,255,0.1) !important;
            backdrop-filter: blur(8px) !important;
            border: 1px solid rgba(255,255,255,0.22) !important;
            border-radius: 6px !important;
            padding: 2px 10px !important;
            margin: 0 !important;
            cursor: pointer !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label[data-baseweb="radio"]:hover {
            background: rgba(8,173,155,0.2) !important;
            border-color: rgba(8,173,155,0.5) !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label[data-baseweb="radio"] span:last-child {
            font-size: 11px !important;
            color: #ffffff !important;
            font-family: 'Inter', sans-serif !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] span[data-baseweb="radio"] {
            display: none !important;
        }
        /* ── Page headings — tighter top margin ── */
        h1 {
            font-family: 'Red Hat Display', 'Inter', sans-serif !important;
            color: #ffffff !important;
            text-shadow: 0 1px 4px rgba(6,62,112,0.3) !important;
            margin-top: 0 !important;
            padding-top: 0 !important;
        }
        h2, h3 {
            font-family: 'Red Hat Display', 'Inter', sans-serif !important;
            color: #ffffff !important;
            text-shadow: 0 1px 4px rgba(6,62,112,0.3) !important;
        }
        /* ── Selectbox text in main area ── */
        [data-testid="stSelectbox"] label { color: #ffffff !important; }
        /* ── Info/success boxes in sidebar ── */
        [data-testid="stSidebar"] [data-testid="stAlert"] {
            background: rgba(8,173,155,0.15) !important;
            border: 1px solid rgba(8,173,155,0.3) !important;
            color: #ffffff !important;
            border-radius: 8px !important;
        }
        </style>
        <script>
        (function() {
            var pd = window.parent.document;
            // ── Fix feedback buttons ──
            function fixFeedbackButtons() {
                var main = window.parent.document.querySelector('[data-testid="stMain"]');
                if (!main) return;
                main.querySelectorAll('.stButton > button').forEach(function(btn) {
                    var txt = btn.innerText.trim();
                    if (txt === 'Yes' || txt === 'No' || txt === 'Yes  \u2713' || txt === 'No  \u2717') {
                        btn.style.setProperty('background',       'transparent', 'important');
                        btn.style.setProperty('background-color', 'transparent', 'important');
                        btn.style.setProperty('background-image', 'none',        'important');
                        btn.style.setProperty('border',           '1px solid rgba(255,255,255,0.35)', 'important');
                        btn.style.setProperty('box-shadow',       'none',        'important');
                        btn.style.setProperty('color',            '#ffffff',     'important');
                        btn.style.setProperty('border-radius',    '6px',         'important');
                        btn.style.setProperty('font-size',        '12px',        'important');
                        btn.style.setProperty('padding',          '2px 10px',    'important');
                    }
                });
            }
                });
            }
            // ── Hide sidebar collapse/expand button ──
            function hideCollapseBtn() {
                [
                    '[data-testid="stSidebarCollapsedControl"]',
                    '[data-testid="collapsedControl"]',
                    '[data-testid="stBaseButton-headerNoPadding"]',
                    'button[aria-label="Collapse sidebar"]',
                    'button[aria-label="Expand sidebar"]',
                    'button[title="Collapse sidebar"]',
                    'button[title="Expand sidebar"]'
                ].forEach(function(sel) {
                    pd.querySelectorAll(sel).forEach(function(el) {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('visibility', 'hidden', 'important');
                        el.style.setProperty('pointer-events', 'none', 'important');
                    });
                });
            }
            // Run both immediately and on every DOM change
            function runAll() { fixFeedbackButtons(); hideCollapseBtn(); }
            runAll();
            var obs = new MutationObserver(runAll);
            function attach() {
                if (pd.body) {
                    obs.observe(pd.body, { childList: true, subtree: true });
                } else {
                    setTimeout(attach, 200);
                }
            }
            attach();
            // Also poll for first 10s to catch late-rendering elements
            var t = 0;
            var poll = setInterval(function() {
                runAll();
                t += 500;
                if (t >= 10000) clearInterval(poll);
            }, 500);
        })();
        </script>
    """)

    # ── Sidebar brand header — Q in red ──────────────────────────────────────
    st.sidebar.markdown(
        f"""
        <div style="padding:4px 0 14px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.12);margin-bottom:12px;">
          <div style="font-family:'Red Hat Display','Inter',sans-serif;font-size:22px;
                      font-weight:700;letter-spacing:0.5px;line-height:1.2;">
            <span style="color:#ffffff;">SOP</span><span style="color:#e53935;">hia</span>
          </div>
          <div style="font-size:10px;color:rgba(255,255,255,0.45);letter-spacing:1px;
                      text-transform:uppercase;margin-top:3px;font-family:'Inter',sans-serif;">
            SOP Assistant
          </div>
        </div>
        <div style="background:rgba(255,255,255,0.1);border-radius:8px;padding:10px 12px;margin-bottom:12px;">
          <div style="font-size:13px;font-weight:500;color:#fff;">{st.session_state.username}</div>
          <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:2px;">Role: {st.session_state.role}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # ── Live user counter (admin only) ────────────────────────────────────────
    if st.session_state.get("role") == "admin":
        count, users = count_active_users()
        user_list_html = "".join(
            f'<div style="font-size:10px;color:rgba(255,255,255,0.6);padding:1px 0;">'
            f'· {u}</div>'
            for u in users
        )
        st.sidebar.html(
            f"""
            <div style="background:rgba(0,188,140,0.15);border:1px solid rgba(0,188,140,0.35);
                        border-radius:8px;padding:10px 12px;margin-bottom:12px;">
              <div style="font-size:11px;color:rgba(255,255,255,0.55);text-transform:uppercase;
                          letter-spacing:0.8px;margin-bottom:4px;">Active Now</div>
              <div style="font-size:22px;font-weight:700;color:#00bc8c;line-height:1;">
                {count}
                <span style="font-size:12px;font-weight:400;color:rgba(255,255,255,0.55);">
                  &nbsp;user{'s' if count != 1 else ''}
                </span>
              </div>
              <div style="margin-top:6px;">{user_list_html}</div>
            </div>
            """
        )

    # ── SOP Filter rendered by show_document_selector() next ──────────────────
    pass


def show_sidebar_bottom():
    """Feedback review + Clear Chat + Logout — rendered AFTER the practice selector."""

    # ── Feedback Review ───────────────────────────────────────────────────────
    st.sidebar.markdown("---")
    entries = load_feedback(limit=100)
    if entries:
        thumbs_up   = sum(1 for e in entries if e.get("rating") == "up")
        thumbs_down = sum(1 for e in entries if e.get("rating") == "down")
        st.sidebar.markdown(
            f"**Feedback** &nbsp; 👍 {thumbs_up} &nbsp; 👎 {thumbs_down}",
            unsafe_allow_html=True
        )
        with st.sidebar.expander("View recent feedback", expanded=False):
            sorted_entries = sorted(entries, key=lambda e: (e.get("timestamp",""), e.get("rating","") == "up"), reverse=True)
            for e in sorted_entries[:20]:
                icon = "👍" if e.get("rating") == "up" else "👎"
                st.markdown(
                    f"{icon} **{e.get('timestamp','')}** — {e.get('user','')}  \n"
                    f"**Practice:** {e.get('practice','')}  \n"
                    f"**Query:** {e.get('query','')}  \n"
                    + (f"**Comment:** {e['comment']}  \n" if e.get('comment') else ""),
                    unsafe_allow_html=False
                )
                st.markdown("---")
    else:
        st.sidebar.caption("No feedback submitted yet.")

    # ── Clear Chat + Logout ───────────────────────────────────────────────────
    st.sidebar.markdown("---")

    if st.sidebar.button("Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.feedback_state = {}
        st.session_state.feedback_comment_open = {}
        st.rerun()

    if st.sidebar.button("Logout", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT + SCOPE SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _build_practice_id_map(schema_dir: str) -> dict:
    """
    Scans data_schema/ for Everhealth JSONs and returns:
        source_file (real name) -> "PRACTICE - #001" label
    Sorted alphabetically so IDs are stable across sessions.
    (Optional masking utility — not wired into the UI by default in this
    sample; kept for reference / demonstration of the pattern.)
    """
    json_files = sorted(glob.glob(os.path.join(schema_dir, "*.json")))
    mapping = {}
    counter = 1
    for fpath in json_files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("team", "") != "Everhealth":
                continue
            source = data.get("source_file", os.path.basename(fpath))
            if source not in mapping:
                mapping[source] = f"PRACTICE - #{counter:03d}"
                counter += 1
        except Exception:
            continue
    return mapping


def _scan_schema(schema_dir: str) -> dict:
    """
    Scans data_schema/ and returns nested dict:
        team -> department -> [source_file, ...]

    For Everhealth:   team_dept_files["Everhealth"][""] = [source_file, ...]
    For Other Vertical: team_dept_files["Other Vertical"]["AR"] = [...]
                                                         ["Billing & Payment"] = [...]
    """
    result = {}
    for fpath in glob.glob(os.path.join(schema_dir, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            team   = data.get("team", "Unknown")
            dept   = data.get("department", "") or ""
            source = data.get("source_file", os.path.basename(fpath))
            result.setdefault(team, {}).setdefault(dept, [])
            result[team][dept].append(source)
        except Exception:
            continue
    return result


def show_document_selector():
    """
    Sidebar selector — adapts based on team:

    Everhealth:
      1. Team selector  → Everhealth (auto if only one team present)
      2. Practice       → real filename (masking removed)

    Other Vertical:
      1. Team selector  → Other Vertical
      2. Department     → AR / Billing & Payment / Coding / Collection
      3. Practice       → real filename

    Returns:
        (selected_doc_file, scope_val)
    """

    schema_dir = os.getenv("SCHEMA_DIR", "./data_schema")

    json_files = glob.glob(os.path.join(schema_dir, "*.json"))
    if not json_files:
        st.sidebar.error("No SOP files found in data_schema folder")
        return None, None

    team_data  = _scan_schema(schema_dir)          # team -> dept -> [source_file]
    practice_id_map = _build_practice_id_map(schema_dir)    # Everhealth source_file list (kept for ordering)

    teams = sorted(team_data.keys())

    # ── Initialise session state ──────────────────────────────────────────────
    for key, default in [
        ("selected_team",            None),
        ("selected_department",      None),
        ("selected_practice",        None),
        ("selected_practice_file",   None),
        ("selected_allowed_sources", None),
        ("selected_scope",           None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Select Team")

    # ── Step 1: Team selector ─────────────────────────────────────────────────
    team_options = ["-- Select Team --"] + teams
    current_team_idx = 0
    if st.session_state.selected_team in teams:
        current_team_idx = teams.index(st.session_state.selected_team) + 1

    chosen_team = st.sidebar.selectbox(
        "Team:",
        options=team_options,
        index=current_team_idx,
        help="Choose Everhealth or Other Vertical"
    )

    if chosen_team == "-- Select Team --":
        st.session_state.selected_team            = None
        st.session_state.selected_department      = None
        st.session_state.selected_practice        = None
        st.session_state.selected_practice_file   = None
        st.session_state.selected_allowed_sources = None
        st.session_state.selected_scope           = None
        st.sidebar.info("Select a team to continue")
        return None, None

    if st.session_state.selected_team != chosen_team:
        st.session_state.selected_team            = chosen_team
        st.session_state.selected_department      = None
        st.session_state.selected_practice        = None
        st.session_state.selected_practice_file   = None
        st.session_state.selected_allowed_sources = None
        st.session_state.selected_scope           = None

    # ══════════════════════════════════════════════════════════════════════════
    # EVERHEALTH PATH — real practice name selector (unmasked)
    # ══════════════════════════════════════════════════════════════════════════
    if chosen_team == "Everhealth":
        all_sources = sorted(practice_id_map.keys())
        practice_options = ["-- Select Practice --"] + all_sources

        current_idx = 0
        if st.session_state.selected_practice in practice_options:
            try:
                current_idx = practice_options.index(st.session_state.selected_practice)
            except ValueError:
                current_idx = 0

        st.sidebar.markdown("### Select Practice")
        chosen_practice = st.sidebar.selectbox(
            "Practice:",
            options=practice_options,
            index=current_idx,
            help="Select a practice"
        )

        if chosen_practice == "-- Select Practice --":
            st.session_state.selected_practice        = None
            st.session_state.selected_practice_file   = None
            st.session_state.selected_allowed_sources = None
            st.session_state.selected_scope           = None
            st.sidebar.info("Select a practice to continue")
            return None, None

        actual_fname = chosen_practice
        if st.session_state.selected_practice != chosen_practice:
            st.session_state.selected_practice      = chosen_practice
            st.session_state.selected_practice_file = actual_fname.lower()
            st.session_state.selected_scope         = None

        st.session_state.selected_practice_file   = actual_fname.lower()
        st.session_state.selected_allowed_sources = None
        st.sidebar.success("Selected")
        st.sidebar.caption(actual_fname)

    # ══════════════════════════════════════════════════════════════════════════
    # OTHER VERTICAL PATH — Department → Practice selector
    # ══════════════════════════════════════════════════════════════════════════
    else:
        dept_data    = team_data.get(chosen_team, {})
        departments  = sorted(dept_data.keys())

        # ── Step 2: Department selector ───────────────────────────────────────
        st.sidebar.markdown("### Select Department")
        dept_options = ["-- Select Department --"] + departments
        current_dept_idx = 0
        if st.session_state.selected_department in departments:
            current_dept_idx = departments.index(st.session_state.selected_department) + 1

        chosen_dept = st.sidebar.selectbox(
            "Department:",
            options=dept_options,
            index=current_dept_idx,
            help="Choose a department (AR, Billing & Payment, Coding, Collection)"
        )

        if chosen_dept == "-- Select Department --":
            st.session_state.selected_department      = None
            st.session_state.selected_practice        = None
            st.session_state.selected_practice_file   = None
            st.session_state.selected_allowed_sources = None
            st.session_state.selected_scope           = None
            st.sidebar.info("Select a department to continue")
            return None, None

        if st.session_state.selected_department != chosen_dept:
            st.session_state.selected_department      = chosen_dept
            st.session_state.selected_practice        = None
            st.session_state.selected_practice_file   = None
            st.session_state.selected_allowed_sources = None
            st.session_state.selected_scope           = None

        # ── Step 3: Practice selector (within department) ─────────────────────
        st.sidebar.markdown("### Select Practice")
        fnames_in_dept  = sorted(dept_data.get(chosen_dept, []))
        practice_options = ["-- Select Practice --"] + fnames_in_dept

        current_practice_idx = 0
        if st.session_state.selected_practice in practice_options:
            try:
                current_practice_idx = practice_options.index(st.session_state.selected_practice)
            except ValueError:
                current_practice_idx = 0

        chosen_practice = st.sidebar.selectbox(
            "Practice:",
            options=practice_options,
            index=current_practice_idx,
            help="Select a practice document"
        )

        if chosen_practice == "-- Select Practice --":
            st.session_state.selected_practice        = None
            st.session_state.selected_practice_file   = None
            st.session_state.selected_allowed_sources = None
            st.session_state.selected_scope           = None
            st.sidebar.info("Select a practice to continue")
            return None, None

        actual_fname = chosen_practice
        if st.session_state.selected_practice != chosen_practice:
            st.session_state.selected_practice      = chosen_practice
            st.session_state.selected_practice_file = actual_fname.lower()
            st.session_state.selected_scope         = None

        st.session_state.selected_practice_file   = actual_fname.lower()
        st.session_state.selected_allowed_sources = None
        st.sidebar.success("Selected")
        st.sidebar.caption(actual_fname)

    # ── Scope / Role selector (shared for both paths) ─────────────────────────
    actual_fname = st.session_state.selected_practice_file or ""
    engine = st.session_state.get("rag_engine")
    scope_val = None

    if engine and actual_fname:
        scopes = engine.get_scopes(actual_fname)
        if len(scopes) > 1:
            st.sidebar.markdown("---")
            st.sidebar.markdown("### Role / Scope")
            scope_options = ["All Scopes"] + scopes
            current_scope_idx = 0
            if st.session_state.selected_scope in scopes:
                current_scope_idx = scopes.index(st.session_state.selected_scope) + 1
            chosen_scope = st.sidebar.selectbox(
                "Select your role:",
                options=scope_options,
                index=current_scope_idx,
                help="Filter results to your role or department"
            )
            scope_val = None if chosen_scope == "All Scopes" else chosen_scope
            st.session_state.selected_scope = scope_val
        elif len(scopes) == 1:
            scope_val = scopes[0]
            st.session_state.selected_scope = scope_val
            st.sidebar.caption(f"Scope: {scope_val}")
        else:
            scope_val = None
            st.session_state.selected_scope = None

    return actual_fname, scope_val

# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONAL LAYER
# Intercepts greetings and non-SOP messages before hitting the RAG engine.
# ══════════════════════════════════════════════════════════════════════════════

import re as _re

_GREETINGS = _re.compile(
    r'^\s*(hi|hello|hey|good\s*(morning|afternoon|evening|day)|howdy|greetings|sup|what'
    r'\'?s\s*up|hiya|yo)\b[\s!?.]*$',
    _re.IGNORECASE
)
_THANKS = _re.compile(
    r'^\s*(thanks?|thank\s*you|thx|cheers|appreciated?|great|awesome|perfect|got\s*it'
    r'|okay|ok|noted|sure|alright|sounds\s*good)[\s!?.]*$',
    _re.IGNORECASE
)
_HOWRU = _re.compile(
    r'^\s*(how\s*are\s*you|how\s*r\s*u|how\s*do\s*you\s*do|how\'?s\s*it\s*going'
    r'|what\'?s\s*new|you\s*good)[\s!?.]*$',
    _re.IGNORECASE
)
_HELP = _re.compile(
    r'^\s*(help|what\s*can\s*you\s*do|what\s*do\s*you\s*do|how\s*do\s*i\s*(use|start)'
    r'|instructions|guide)[\s!?.]*$',
    _re.IGNORECASE
)


def _conversational_reply(prompt: str, username: str, selected_doc: str) -> str | None:
    """
    Returns a short conversational reply if the message is a greeting,
    thanks, or non-SOP message. Returns None if it should go to the RAG engine.
    """
    p = prompt.strip()

    if _GREETINGS.match(p):
        doc_hint = (f"I have **{selected_doc}** loaded."
                    if selected_doc else "Please select a practice from the sidebar first.")
        return (
            f"Hi {username.title()}! {doc_hint}\n\n"
            f"What would you like to know from the SOP?"
        )

    if _HOWRU.match(p):
        return "Doing well, thanks for asking. What SOP question can I help you with?"

    if _THANKS.match(p):
        return "You're welcome. Let me know if you have any other SOP questions."

    if _HELP.match(p):
        return (
            "I can answer questions from your selected SOP document.\n\n"
            "Try asking things like:\n"
            "- *Can the patient be billed?*\n"
            "- *Payment posting rules*\n"
            "- *List of holidays*\n"
            "- *Coding denials*\n"
            "- *Latest update*\n\n"
            "Select a practice from the sidebar, then type your question."
        )

    return None  # not a conversational message — send to RAG engine


# ── Vague query detection ─────────────────────────────────────────────────────
# These patterns detect broad/incomplete queries that need clarification.
# Each entry: (regex, clarifying question to ask back)
_VAGUE_QUERIES = [
    (
        _re.compile(
            r'^\s*(claim\s*(denied?|rejection?|rejected?|not\s*paid?|unpaid?)'
            r'|denied?\s*claim|denial|denials?)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can help with denied claims. Could you tell me more?\n\n"
            "- **What was the denial reason?** *(e.g. CO-97, CO-4, PR-1, timely filing, "
            "duplicate, not medically necessary, missing auth)*\n"
            "- **Which payer denied it?**\n"
            "- **What type of service was billed?**\n\n"
            "The more detail you share, the more specific the SOP guidance I can find."
        )
    ),
    (
        _re.compile(
            r'^\s*(auth(orization)?|prior\s*auth|pre[\s\-]?auth|auth\s*issue|auth\s*required?)'
            r'\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can look up authorization procedures. A few questions:\n\n"
            "- **Is this about checking if auth is required**, or **how to obtain auth**?\n"
            "- **Which payer or plan?** *(e.g. Aetna, BCBS, Medicare)*\n"
            "- **Which service or CPT code?**\n\n"
            "Please share these details and I'll find the right SOP steps."
        )
    ),
    (
        _re.compile(
            r'^\s*(billing\s*(issue|problem|error|question)?'
            r'|billing\s*[?.!]*'
            r'|charge\s*(entry|question)?'
            r'|bill(ing)?)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "Happy to help with billing. Could you be more specific?\n\n"
            "- **What is the billing question?** *(e.g. Can the patient be billed? "
            "Which charges to submit? Coordination of benefits?)*\n"
            "- **Which payer or insurance plan?**\n"
            "- **Which service or provider?**"
        )
    ),
    (
        _re.compile(
            r'^\s*(payment\s*(issue|problem|posting?|question)?'
            r'|payment\s*[?.!]*'
            r'|posted?\s*(payment)?|eob|era)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can help with payment questions. Could you clarify?\n\n"
            "- **Is this about posting a payment**, resolving an underpayment, "
            "or a patient payment?\n"
            "- **Which payer sent the payment?**\n"
            "- **Is this an ERA/EFT or paper check?**"
        )
    ),
    (
        _re.compile(
            r'^\s*(eligibility|coverage|insurance\s*check|benefit\s*(check|verification)?'
            r'|verify\s*(insurance|eligibility|coverage)?)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can find eligibility verification steps. A couple of questions:\n\n"
            "- **Which payer or plan?** *(e.g. Medicare, Medicaid, commercial)*\n"
            "- **Is this for a new patient, renewal, or specific service type?**"
        )
    ),
    (
        _re.compile(
            r'^\s*(ar|accounts?\s*receivable|follow[\s\-]?up|follow\s*up\s*call?'
            r'|collections?)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can help with AR and follow-up procedures. Please clarify:\n\n"
            "- **Which payer or claim type?** *(e.g. Medicare, commercial, patient balance)*\n"
            "- **How old is the claim?** *(e.g. 30 days, 90 days, approaching timely filing)*\n"
            "- **Has the claim been submitted, partially paid, or not yet responded to?**"
        )
    ),
    (
        _re.compile(
            r'^\s*(coding|code|cpt|icd|hcpcs|modifier)\s*[?.!]*\s*$',
            _re.IGNORECASE
        ),
        (
            "I can look up coding guidelines. Could you be more specific?\n\n"
            "- **What is the specific code or service?** *(e.g. CPT 99213, G0008)*\n"
            "- **What is the coding question?** *(e.g. When to use modifier 25? "
            "Can these two codes be billed together?)*\n"
            "- **Which payer?**"
        )
    ),
]


def _vague_query_reply(prompt: str) -> str | None:
    """
    Detects vague one-word or one-topic queries and returns a clarifying question.
    Returns None if the query is specific enough to send to the engine.
    """
    for pattern, clarification in _VAGUE_QUERIES:
        if pattern.match(prompt.strip()):
            return clarification
    return None

def show_chat():
    """Main chat interface"""

    st.html("<style>[data-testid='stMainBlockContainer']{padding-top:0.4rem!important}</style>")
    st.markdown(
        """
        <h1 style="font-family:'Red Hat Display','Inter',sans-serif;font-weight:800;
                    font-size:2.25rem;letter-spacing:0.5px;margin:0 0 0.6rem;">
          <span style="color:#ffffff;">SOP</span><span style="color:#e53935;">hia</span>
        </h1>
        """,
        unsafe_allow_html=True,
    )

    # ── Active document + scope banner ────────────────────────────────────────
    selected_doc        = st.session_state.get("selected_practice")         # display label
    selected_doc_file   = st.session_state.get("selected_practice_file")    # single-doc filename
    selected_scope      = st.session_state.get("selected_scope")
    selected_sources    = st.session_state.get("selected_allowed_sources")  # multi-doc list
    selected_team       = st.session_state.get("selected_team")

    is_multi = selected_sources is not None   # All Practices mode
    has_selection = is_multi or bool(selected_doc_file)

    selected_dept = st.session_state.get("selected_department")

    # Real practice name label (masking removed) — same for Everhealth and Other Vertical
    masked_label = selected_doc if selected_doc else selected_doc_file

    if has_selection:
        if is_multi:
            banner_doc = f"All Practices ({len(selected_sources)} docs)"
            st.markdown(
                f'''<div style="background:rgba(255,255,255,0.15);backdrop-filter:blur(8px);
                border:1px solid rgba(8,173,155,0.4);border-radius:10px;
                padding:8px 16px;margin-bottom:10px;font-size:13px;color:#ffffff;
                box-shadow:0 2px 8px rgba(6,62,112,0.15);">
                <b>Scope:</b> {banner_doc}
                </div>''',
                unsafe_allow_html=True
            )
        else:
            scope_text = f" &nbsp;|&nbsp; Role: <b>{selected_scope}</b>" if selected_scope else ""
            dept_text  = f" &nbsp;|&nbsp; Dept: <b>{selected_dept}</b>" if selected_dept else ""
            st.markdown(
                f'''<div style="background:rgba(255,255,255,0.15);backdrop-filter:blur(8px);
                border:1px solid rgba(8,173,155,0.4);border-radius:10px;
                padding:8px 16px;margin-bottom:10px;font-size:13px;color:#ffffff;
                box-shadow:0 2px 8px rgba(6,62,112,0.15);">
                <b>Practice:</b> {masked_label}{dept_text}{scope_text}
                </div>''',
                unsafe_allow_html=True
            )
    else:
        st.markdown("Select a team and practice from the sidebar, then ask a question.")

    # ── Helper: render feedback widget for one assistant message ────────────
    def _render_feedback(msg_idx: int, query: str, answer: str, practice: str):
        """Was this helpful? Yes / No — rendered as horizontal radio (no column gaps)."""
        current = st.session_state.feedback_state.get(msg_idx)

        # Map stored state → radio index so selection persists across reruns
        default_idx = {"up": 0, "down": 1}.get(current, None)

        choice = st.radio(
            "Was this helpful?",
            options=["Yes", "No"],
            index=default_idx,
            horizontal=True,
            key=f"fb_radio_{msg_idx}",
        )

        if choice == "Yes" and current != "up":
            st.session_state.feedback_state[msg_idx] = "up"
            st.session_state.feedback_comment_open[msg_idx] = False
            save_feedback("up", query, answer, practice,
                          username=st.session_state.get("username", ""))
            st.toast("Thank you for your feedback.")
            st.rerun()

        if choice == "No" and current != "down":
            st.session_state.feedback_state[msg_idx] = "down"
            st.session_state.feedback_comment_open[msg_idx] = True
            st.rerun()

        # Detail form — shown when No is selected
        if st.session_state.feedback_comment_open.get(msg_idx, False):
            with st.form(key=f"fb_form_{msg_idx}", clear_on_submit=True):
                st.markdown(
                    "<span style='font-size:12px;color:rgba(255,255,255,0.7);'>"
                    "Please describe the issue so we can improve accuracy:</span>",
                    unsafe_allow_html=True,
                )
                comment = st.text_area(
                    "",
                    placeholder="e.g. Step 3 is missing, wrong practice referenced, outdated procedure...",
                    key=f"fb_text_{msg_idx}",
                    height=80,
                    label_visibility="collapsed",
                )
                submitted = st.form_submit_button("Submit Report")
                if submitted:
                    save_feedback("down", query, answer, practice,
                                  comment=comment,
                                  username=st.session_state.get("username", ""))
                    st.session_state.feedback_comment_open[msg_idx] = False
                    st.toast("Report submitted. The team will review this response.")

    # ── Display chat history (with feedback for past assistant messages) ──────
    username    = st.session_state.get("username", "")
    practice_label = (
        f"All Practices" if is_multi
        else (
            f"{masked_label} [{selected_dept}]" if selected_dept and masked_label
            else (masked_label or "Unknown")
        )
    )

    assistant_indices = []   # track which message indices are assistant responses
    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("is_rag"):
            assistant_indices.append(i)
            _render_feedback(
                msg_idx  = i,
                query    = msg.get("query", ""),
                answer   = msg["content"],
                practice = msg.get("practice", practice_label),
            )

    # Chat input
    if prompt := st.chat_input("Ask about SOPs..."):

        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Scroll to show user message immediately
        st.components.v1.html(
            "<script>setTimeout(function(){window.parent.scrollTo({top:window.parent.document.body.scrollHeight,behavior:'smooth'});},100);</script>",
            height=0,
        )

        # ── Conversational layer — intercept greetings/thanks before RAG ──────
        conv_reply = _conversational_reply(
            prompt,
            st.session_state.get("username", ""),
            selected_doc_file or (f"All Practices – {selected_team}" if is_multi else "")
        )

        if conv_reply:
            with st.chat_message("assistant"):
                st.markdown(conv_reply)
            st.session_state.messages.append({"role": "assistant", "content": conv_reply})
            st.stop()

        # ── Vague query detection — ask for clarification instead of loading ──
        vague_reply = _vague_query_reply(prompt)
        if vague_reply:
            with st.chat_message("assistant"):
                st.markdown(vague_reply)
            st.session_state.messages.append({"role": "assistant", "content": vague_reply})
            st.stop()

        # ── RAG query — requires document selection ───────────────────────────
        if not has_selection:
            reply = "Please select a practice from the sidebar, then ask your question."
            with st.chat_message("assistant"):
                st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.stop()

        # ── Build conversational context from chat history ────────────────────
        # Only include the immediately preceding turn if the bot asked a
        # clarifying question (is_rag=False means it was a conversational reply
        # or vague-query clarification). This lets "CO-97" resolve back to
        # "denied claim" without flooding the filter with irrelevant text.
        enriched_query = prompt   # default: just the current message

        msgs = st.session_state.messages
        # Look at the last assistant message before this user message
        prior_messages = [m for m in msgs[:-1] if m["role"] in ("user", "assistant")]
        if prior_messages:
            last_assistant = next(
                (m for m in reversed(prior_messages) if m["role"] == "assistant"), None
            )
            last_user = next(
                (m for m in reversed(prior_messages) if m["role"] == "user"), None
            )
            # Only enrich if the last assistant turn was a clarifying question
            # (not_rag = conversational/vague reply), and current prompt is short
            # (i.e. the user is answering the clarification, not asking a new Q)
            _GREETINGS = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "sure", "bye"}
            prev_was_greeting = (
                last_user and last_user["content"].strip().lower() in _GREETINGS
            )
            is_followup = (
                last_assistant
                and not last_assistant.get("is_rag", False)
                and last_user
                and not prev_was_greeting
                and len(prompt.strip()) < 120
            )
            if is_followup:
                # Combine: previous user question + current answer
                prev_q = last_user["content"].strip()
                enriched_query = f"{prev_q} {prompt}".strip()
                print(f"[Chat] Follow-up detected. Enriched: '{enriched_query[:80]}'")
            # else: treat as a fresh independent query

        # Get response from RAG engine
        with st.chat_message("assistant"):
            if is_multi:
                stream_gen = st.session_state.rag_engine.get_answer_stream(
                    enriched_query,
                    allowed_sources=selected_sources,
                    scope=selected_scope
                )
            else:
                stream_gen = st.session_state.rag_engine.get_answer_stream(
                    enriched_query,
                    practice_name=selected_doc_file,
                    scope=selected_scope
                )

            # Stream tokens — collect meta from final yield
            answer_tokens = []
            result = {"sources": [], "error": False}
            placeholder = st.empty()
            full_text = ""

            # ── Spinning gradient loader — teal only, no text ────────────────
            _LOADER_HTML = """
            <div style="padding:8px 4px;">
              <div style="position:relative;width:36px;height:36px;">
                <div style="
                    position:absolute;inset:0;border-radius:50%;
                    background:conic-gradient(from 0deg,#08ad9b,#0891b2,#063e70,transparent);
                    animation:sop-spin 0.85s linear infinite;"></div>
                <div style="
                    position:absolute;inset:4px;border-radius:50%;
                    background:rgba(10,90,110,0.85);
                    backdrop-filter:blur(4px);"></div>
                <div style="
                    position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);
                    width:6px;height:6px;border-radius:50%;
                    background:#08ad9b;
                    box-shadow:0 0 8px rgba(8,173,155,1);
                    animation:sop-pulse 0.85s ease-in-out infinite;"></div>
              </div>
            </div>
            <style>
              @keyframes sop-spin  { to { transform:rotate(360deg); } }
              @keyframes sop-pulse { 0%,100%{opacity:.5;transform:translate(-50%,-50%) scale(.9);}
                                     50%{opacity:1;transform:translate(-50%,-50%) scale(1.3);} }
            </style>
            """
            placeholder.html(_LOADER_HTML)

            for token, meta in stream_gen:
                if meta:
                    result = meta
                    if meta.get("error") or "answer" in meta:
                        answer = meta.get("answer", "")
                        if meta.get("error"):
                            placeholder.empty()
                            st.error(answer)
                        else:
                            placeholder.markdown(answer)
                        full_text = answer
                else:
                    full_text += token
                    placeholder.markdown(full_text + "▌")

            # Final render without cursor
            if full_text and not result.get("error"):
                placeholder.markdown(full_text)
            answer = full_text

            if result.get("sources"):
                with st.expander("View Sources"):
                    for src in result["sources"]:
                        src_name = src.get("source", "Unknown")
                        # Only warn about unexpected sources in single-doc mode
                        if not is_multi and src_name.lower() != (selected_doc_file or '').lower():
                            st.warning(
                                f"Result came from **{src_name}** instead of "
                                f"**{selected_doc_file}** — check RAG engine filter."
                            )
                        st.markdown(f"**Source:** {src_name}")
                        st.markdown(f"**Location:** {src.get('page', 'Unknown')}")
                        if src.get("score"):
                            st.markdown(f"**Relevance:** {src['score']:.3f}")
                        st.markdown("---")

        # Store message with metadata for feedback
        msg_idx = len(st.session_state.messages)
        st.session_state.messages.append({
            "role":     "assistant",
            "content":  answer,
            "query":    prompt,
            "practice": practice_label,
            "is_rag":   True,
        })
        # Render feedback buttons immediately for the new message
        _render_feedback(msg_idx, prompt, answer, practice_label)

        # ── Auto-scroll to bottom after answer is rendered ────────────────────
        st.components.v1.html(
            """
            <script>
                // Wait for Streamlit to finish rendering then scroll
                function scrollToBottom() {
                    // Target the main scrollable area in Streamlit
                    const containers = [
                        window.parent.document.querySelector('[data-testid="stAppViewContainer"]'),
                        window.parent.document.querySelector('.main'),
                        window.parent.document.querySelector('[data-testid="stMain"]'),
                        window.parent.document.documentElement,
                        window.parent.document.body,
                    ];
                    for (const el of containers) {
                        if (el) {
                            el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
                        }
                    }
                    // Also scroll the window itself
                    window.parent.scrollTo({ top: window.parent.document.body.scrollHeight, behavior: 'smooth' });
                }
                // Run immediately and again after a short delay to catch late renders
                scrollToBottom();
                setTimeout(scrollToBottom, 300);
                setTimeout(scrollToBottom, 800);
            </script>
            """,
            height=0,
        )

# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """Main application logic"""

    check_reload_flag()   # zero-downtime SOP reload — checks flag from sop_watcher.py
    initialize_session()

    if not st.session_state.authenticated:
        show_login()
        return

    update_active_session()
    show_sidebar()
    selected_doc, selected_scope = show_document_selector()
    show_sidebar_bottom()
    show_chat()

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()