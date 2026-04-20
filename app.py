import streamlit as st
import requests
import json
import uuid
import base64
import io
import re
from fpdf import FPDF

CHAT_API = "https://8ceb8k79sf.execute-api.ap-south-1.amazonaws.com/v1/sisyphus/chatbot"
HISTORY_API = "https://8ceb8k79sf.execute-api.ap-south-1.amazonaws.com/v1/sisyphus/chatHistory"


def call_api(url, payload):
    """Make POST API call and unwrap nested body responses."""
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
    data = resp.json()
    while isinstance(data.get("body"), str):
        try:
            data = json.loads(data["body"])
        except (json.JSONDecodeError, TypeError):
            break
    return data


def call_get_api(url, params=None):
    """Make GET API call and unwrap nested body responses."""
    resp = requests.get(url, params=params, headers={"Content-Type": "application/json"}, timeout=60)
    print(f"GET {url} params={params} status={resp.status_code} response={resp.text[:500]}")
    data = resp.json()
    while isinstance(data.get("body"), str):
        try:
            data = json.loads(data["body"])
        except (json.JSONDecodeError, TypeError):
            break
    return data


def call_delete_api(url, params=None):
    """Make DELETE API call and unwrap nested body responses."""
    resp = requests.delete(url, params=params, headers={"Content-Type": "application/json"}, timeout=60)
    print(f"DELETE {url} params={params} status={resp.status_code} response={resp.text[:500]}")
    data = resp.json()
    while isinstance(data.get("body"), str):
        try:
            data = json.loads(data["body"])
        except (json.JSONDecodeError, TypeError):
            break
    return data


def extract_answer(data):
    """Extract answer text from various response formats."""
    if "answer" in data:
        answer = data["answer"]
        if isinstance(answer, str):
            try:
                parsed = json.loads(answer)
                if "choices" in parsed:
                    return parsed["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        return answer
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return f"Unexpected response: {json.dumps(data)[:300]}"


st.set_page_config(page_title="Sisyphus", page_icon="S", layout="wide")


def sanitize_text(text: str) -> str:
    """Replace Unicode characters with ASCII equivalents for PDF compatibility."""
    replacements = {
        "\u2013": "-",   # en dash
        "\u2014": "-",   # em dash
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2026": "...", # ellipsis
        "\u2022": "-",   # bullet
        "\u00b5": "u",   # micro sign
        "\u03bc": "u",   # mu
        "\u2264": "<=",  # less than or equal
        "\u2265": ">=",  # greater than or equal
        "\u00b0": " deg",# degree
        "\u2192": "->",  # right arrow
        "\u2190": "<-",  # left arrow
        "\u00d7": "x",   # multiplication sign
        "\u00f7": "/",   # division sign
        "\u2248": "~",   # approximately
        "\u2260": "!=",  # not equal
        "\u00ae": "(R)", # registered
        "\u2122": "(TM)",# trademark
        "\u00a9": "(C)", # copyright
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Remove any remaining non-latin1 characters
    return text.encode("latin-1", errors="replace").decode("latin-1")


def safe_write(pdf, h, text):
    """Write text to PDF safely, handling any edge cases."""
    text = sanitize_text(text.strip())
    if not text:
        return
    # Ensure we're at left margin with full width available
    pdf.set_x(pdf.l_margin)
    available = pdf.w - pdf.l_margin - pdf.r_margin
    if available < 10:
        return
    try:
        pdf.multi_cell(w=available, h=h, text=text)
    except Exception:
        # If it still fails, write in small chunks
        try:
            chunk_size = 80
            for i in range(0, len(text), chunk_size):
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(w=available, h=h, text=text[i:i+chunk_size])
        except Exception:
            pass


def generate_pdf(question: str, answer: str) -> bytes:
    """Generate a PDF from the answer text."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    page_width = pdf.w - pdf.l_margin - pdf.r_margin

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(26, 26, 46)
    pdf.cell(0, 12, "Sisyphus - AI Teaching Assistant", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # Question
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(0, 8, "Question:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    safe_write(pdf, 6, question)
    pdf.ln(4)

    # Answer
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(0, 8, "Answer:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(30, 30, 30)

    # Clean markdown from answer and write as plain text
    clean_answer = answer
    # Remove markdown headers
    clean_answer = re.sub(r"^#{1,3}\s+", "", clean_answer, flags=re.MULTILINE)
    # Remove bold markers
    clean_answer = re.sub(r"\*\*(.*?)\*\*", r"\1", clean_answer)
    # Remove italic markers
    clean_answer = re.sub(r"\*(.*?)\*", r"\1", clean_answer)

    for line in clean_answer.split("\n"):
        stripped = line.strip()
        if not stripped:
            pdf.ln(3)
            continue
        safe_write(pdf, 6, stripped)

    return bytes(pdf.output())


@st.dialog("Sisyphus - Answer PDF", width="large")
def show_pdf_dialog(question: str, answer: str, key_suffix: str):
    """Fullscreen PDF viewer dialog with download."""
    pdf_bytes = generate_pdf(question, answer)
    b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    st.download_button(
        label="Download PDF",
        data=pdf_bytes,
        file_name=f"sisyphus_answer_{key_suffix[:8]}.pdf",
        mime="application/pdf",
        key=f"dl_pdf_dialog_{key_suffix}"
    )

    pdf_display = f'<iframe src="data:application/pdf;base64,{b64_pdf}" width="100%" height="600" style="border: none; border-radius: 8px;"></iframe>'
    st.markdown(pdf_display, unsafe_allow_html=True)


def render_pdf_buttons(question: str, answer: str, key_suffix: str):
    """Render a single View PDF button that opens a dialog."""
    if st.button("View PDF", key=f"view_pdf_{key_suffix}"):
        show_pdf_dialog(question, answer, key_suffix)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Main background white */
.stApp { background-color: #ffffff; }

/* Always show sidebar collapse/expand arrow */
[data-testid="collapsedControl"] {
    display: flex !important;
    position: fixed;
    top: 0.5rem;
    left: 0.5rem;
    z-index: 999;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 0.3rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

/* Sidebar white theme */
[data-testid="stSidebar"] { 
    background: #ffffff; 
    border-right: 1px solid #e5e7eb; 
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
    color: #374151;
}

/* Button styling */
.stButton > button {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    color: #374151;
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    background: #f9fafb;
    border-color: #d1d5db;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
    background: #4f46e5 !important;
    border-color: #4f46e5 !important;
    color: #ffffff !important;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover {
    background: #4338ca !important;
    border-color: #4338ca !important;
    color: #ffffff !important;
}

/* Sidebar New Chat button specific */
[data-testid="stSidebar"] .stButton > button[kind="primary"],
[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
    background: #4f46e5 !important;
    color: #ffffff !important;
    border: none !important;
    padding: 0.6rem 1rem;
    font-weight: 600;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] p,
[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] p {
    color: #ffffff !important;
}
</style>
""", unsafe_allow_html=True)

# --- Session state ---
if "chat_id" not in st.session_state:
    st.session_state.chat_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "chat_list" not in st.session_state:
    st.session_state.chat_list = []
if "chats_loaded" not in st.session_state:
    st.session_state.chats_loaded = False


def load_chat_list():
    try:
        data = call_get_api(HISTORY_API, {"action": "get_chats"})
        st.session_state.chat_list = data.get("chats", [])
    except Exception as e:
        print(f"Error loading chat list: {e}")
        st.session_state.chat_list = []
    st.session_state.chats_loaded = True


def load_chat_history(chat_id):
    try:
        data = call_get_api(HISTORY_API, {"action": "get_history", "chat_id": chat_id})
        st.session_state.messages = [
            {"role": m["role"], "content": m["content"]}
            for m in data.get("messages", [])
        ]
        st.session_state.chat_id = chat_id
    except Exception as e:
        print(f"Error loading chat history: {e}")
        st.session_state.messages = []


def start_new_chat():
    st.session_state.chat_id = str(uuid.uuid4())
    st.session_state.messages = []


def delete_chat(chat_id):
    try:
        call_delete_api(HISTORY_API, {"chat_id": chat_id})
    except Exception:
        pass
    if st.session_state.chat_id == chat_id:
        start_new_chat()
    st.session_state.chats_loaded = False


if not st.session_state.chats_loaded:
    load_chat_list()

# --- Sidebar ---
with st.sidebar:
    st.markdown("""
    <style>
    .sidebar-title { 
        font-size: 1.3rem; 
        font-weight: 700; 
        color: #111827; 
        padding: 0.75rem 0; 
        letter-spacing: -0.02em;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sidebar-title">Sisyphus</div>', unsafe_allow_html=True)

    if st.button("New Chat", use_container_width=True, type="primary"):
        start_new_chat()
        st.rerun()

    st.divider()

    if st.session_state.chat_list:
        for chat in st.session_state.chat_list:
            cid = chat["chat_id"]
            title = chat.get("title", "Untitled")
            is_active = cid == st.session_state.chat_id

            col1, col2 = st.columns([5, 1])
            with col1:
                label = f">> {title}" if is_active else title
                if st.button(label, key=f"chat_{cid}", use_container_width=True):
                    load_chat_history(cid)
                    st.rerun()
            with col2:
                if st.button("x", key=f"del_{cid}"):
                    delete_chat(cid)
                    st.rerun()
    else:
        st.caption("No chat history yet")

# --- Main chat area ---
st.markdown("""
<style>
.block-container { padding-top: 1.5rem; max-width: 850px; }

/* Chat messages - clean white theme */
.stChatMessage { 
    border-radius: 12px; 
    margin-bottom: 0.75rem; 
    font-size: 0.95rem; 
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: #f9fafb; 
    border: 1px solid #e5e7eb;
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    background: #ffffff; 
    border: 1px solid #e5e7eb;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}

/* Chat input */
[data-testid="stChatInput"] { 
    border-color: #e5e7eb; 
    background: #ffffff;
    border-radius: 12px;
}
[data-testid="stChatInput"]:focus-within {
    border-color: #4f46e5; 
    box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

/* Welcome card - white theme */
.welcome-card {
    background: #ffffff;
    border: 1px solid #e5e7eb; 
    border-radius: 16px;
    padding: 2.5rem; 
    text-align: center; 
    margin: 2rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.welcome-card h3 { 
    color: #111827; 
    font-weight: 600; 
    margin-bottom: 0.5rem; 
    font-size: 1.25rem; 
}
.welcome-card p { 
    color: #6b7280; 
    font-size: 0.95rem; 
    margin: 0; 
}
.suggestion-chips { 
    display: flex; 
    flex-wrap: wrap; 
    gap: 0.5rem; 
    justify-content: center; 
    margin-top: 1.25rem; 
}
.chip { 
    background: #f9fafb; 
    border: 1px solid #e5e7eb; 
    border-radius: 20px; 
    padding: 0.5rem 1rem; 
    font-size: 0.85rem; 
    color: #374151; 
    transition: all 0.15s ease;
}
.chip:hover {
    background: #f3f4f6;
    border-color: #d1d5db;
}

/* Divider */
hr {
    border-color: #e5e7eb;
}
</style>
""", unsafe_allow_html=True)

if not st.session_state.messages:
    st.markdown("""
    <div class="welcome-card">
        <h3>What would you like to learn today?</h3>
        <p>Ask anything about your course material.</p>
        <div class="suggestion-chips">
            <span class="chip">Ask anything related Lecture</span>
            <span class="chip">Explain key concepts</span>
            <span class="chip">Get detailed explanation</span>
        </div>
    </div>
    """, unsafe_allow_html=True)


# Display chat messages
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            question = ""
            if idx > 0 and st.session_state.messages[idx - 1]["role"] == "user":
                question = st.session_state.messages[idx - 1]["content"]
            render_pdf_buttons(question, msg["content"], f"hist_{idx}")

# Chat input
if prompt := st.chat_input("Type your question here..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Generating Answer..."):
            try:
                message_id = str(uuid.uuid4())
                data = call_api(CHAT_API, {
                    "query": prompt,
                    "chat_id": st.session_state.chat_id,
                    "message_id": message_id
                })
                answer = extract_answer(data)
                st.markdown(answer)
                render_pdf_buttons(prompt, answer, message_id)
            except requests.exceptions.Timeout:
                answer = "The request timed out. Please try again."
                st.markdown(answer)
            except Exception as e:
                answer = f"Something went wrong: {str(e)}"
                st.markdown(answer)

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer
    })
    st.session_state.chats_loaded = False
