Python 3.13.2 (v3.13.2:4f8bb3947cf, Feb  4 2025, 11:51:10) [Clang 15.0.0 (clang-1500.3.9.4)] on darwin
Type "help", "copyright", "credits" or "license()" for more information.
# app.py
import os
import base64
import mimetypes
import pandas as pd
import streamlit as st

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------- CONFIG ----------
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]
DEFAULT_SHEET_ID = "1Wo3m-mJnRT-qZKn34fs3gAD17e6rto54XcqfGYwnvf0"  # fallback if not in secrets
SHEET_ID = st.secrets.get("SHEET_ID", DEFAULT_SHEET_ID)
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "")

# Session state to track this session's draft IDs
if "draft_ids" not in st.session_state:
    st.session_state["draft_ids"] = []

# ---------- AUTH (secrets-based; one refresh token for both APIs) ----------
def creds_from_secrets(scopes):
    return Credentials(
        token=None,
        refresh_token=st.secrets["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["GOOGLE_CLIENT_ID"],
        client_secret=st.secrets["GOOGLE_CLIENT_SECRET"],
        scopes=scopes,
    )

def get_services():
    creds = creds_from_secrets(SCOPES)
    gmail = build("gmail", "v1", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return gmail, sheets

# ---------- DATA ----------
def load_sheet_data(sheet_service, sheet_name="Sheet1"):
    """
    Expects columns: 이름, 전자 메일 주소, 직함, 친구, plus deal-type columns.
    """
    rng = f"{sheet_name}"
    result = sheet_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=rng
    ).execute()
    data = result.get("values", [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data[1:], columns=data[0])
    df.columns = df.columns.str.strip()
    return df

# ---------- UTIL ----------
def trim_first_syllable(name: str) -> str:
    return name[1:] if isinstance(name, str) and len(name) > 1 else name or ""

def build_mime_with_attachments(to_, subject_, body_, files):
    msg = MIMEMultipart()
    msg["to"] = to_
    msg["subject"] = subject_
    msg.attach(MIMEText(body_ or "", "plain"))

    if files:
        for f in files:
            # Streamlit UploadedFile: reset pointer for each use
            f.seek(0)
            content = f.read()
            filename = f.name or "attachment"
            content_type, encoding = mimetypes.guess_type(filename)
            if content_type is None or encoding is not None:
                content_type = "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)

            part = MIMEBase(main_type, sub_type)
            part.set_payload(content)
            encoders.encode_base64(part)
            # Critical headers to avoid "attach.txt"
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            part.add_header("Content-Type", f'{main_type}/{sub_type}; name="{filename}"')
            msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"raw": raw}

def create_draft(gmail_service, to, subject, body, files=None):
    raw = build_mime_with_attachments(to, subject, body, files)
    draft = gmail_service.users().drafts().create(userId="me", body={"message": raw}).execute()
    return draft.get("id")

def send_drafts(gmail_service, draft_ids):
    for did in draft_ids:
        gmail_service.users().drafts().send(userId="me", body={"id": did}).execute()

# ---------- APP ----------
def main():
    st.set_page_config(page_title="📧 Personalized Email Draft App", layout="centered")
    st.title("📧 Personalized Email Draft App")

    # Connect to Google APIs
    try:
        gmail_service, sheet_service = get_services()
    except Exception as e:
        st.error("Google API authentication failed. Check Streamlit Secrets.")
        st.exception(e)
        return

    # Controls
    sheet_name = st.text_input("Google Sheet tab name", "Sheet1")
    df = load_sheet_data(sheet_service, sheet_name=sheet_name)

    if df.empty:
        st.warning("No data found in the sheet. Check Sheet ID, tab name, and permissions.")
        return

    required = {"이름", "전자 메일 주소", "직함", "친구"}
    missing = required - set(df.columns)
    if missing:
        st.error(f"Missing required columns in sheet: {', '.join(missing)}")
        st.dataframe(df.head())
        return

    friend_filter = st.selectbox("친구 여부", ["친구", "친구 아님"])
    deal_types = [c for c in df.columns if c not in ["이름", "전자 메일 주소", "직함", "친구"]]
    if not deal_types:
        st.warning("No deal-type columns detected. Add some columns (e.g., 신주, 구주).")
        deal_types = ["신주", "구주"]  # fallback UI choice
    deal_filter = st.selectbox("📂 딜 종류", deal_types)
    remove_suffix = st.checkbox("접미사 (님/아/야) 제거 + 제목도 제거")

    subject_input = st.text_input("이메일 제목", "투자 제안 관련 건")
    body_input = st.text_area("이메일 본문")
    file_inputs = st.file_uploader("📎 Attach files (optional)", accept_multiple_files=True)

    # Filtering
...     if friend_filter == "친구":
...         filtered_df = df[df["친구"].astype(str).str.strip().ne("")]
...     else:
...         filtered_df = df[df["친구"].isna() | df["친구"].astype(str).str.strip().eq("")]
... 
...     if deal_filter in filtered_df.columns:
...         filtered_df = filtered_df[
...             filtered_df[deal_filter].astype(str).str.strip().ne("")
...         ].copy()
...     else:
...         st.warning(f"'{deal_filter}' column not found; showing all after 친구 filter.")
...         filtered_df = filtered_df.copy()
... 
...     # Preview rows → compose subjects
...     preview_rows = []
...     for _, row in filtered_df.iterrows():
...         name = str(row.get("이름", "")).strip()
...         email = str(row.get("전자 메일 주소", "")).strip()
...         position = str(row.get("직함", "")).strip()
... 
...         if remove_suffix:
...             subject = subject_input
...         else:
...             if friend_filter == "친구":
...                 trimmed = trim_first_syllable(name)
...                 # Korean 받침 check: (codepoint-0xAC00) % 28 -> 0 means no 받침
...                 suffix = "아" if trimmed and ((ord(trimmed[-1]) - 0xAC00) % 28) else "야"
...                 subject = f"{trimmed}{suffix}, {subject_input}"
...             else:
...                 subject = f"{position}님, {subject_input}" if position else subject_input
... 
...         preview_rows.append({"이메일": email, "제목": subject, "본문": body_input})
... 
...     preview_df = pd.DataFrame(preview_rows)
...     st.subheader("미리보기")
...     st.dataframe(preview_df, use_container_width=True)
... 
...     # Actions
...     col1, col2, col3 = st.columns([1,1,1])
...     with col1:
...         if st.button("💾 Save as Drafts"):
...             st.session_state["draft_ids"].clear()
...             for _, row in preview_df.iterrows():
...                 if not row["이메일"]:
...                     continue
...                 did = create_draft(
...                     gmail_service,
...                     to=row["이메일"],
...                     subject=row["제목"],
...                     body=row["본문"],
...                     files=file_inputs,
...                 )
...                 if did:
...                     st.session_state["draft_ids"].append(did)
...             st.success(f"Drafts created: {len(st.session_state['draft_ids'])}")
... 
...     with col2:
...         send_confirm = st.checkbox("✅ Confirm send")
...         if st.button("📤 Send Drafts (this session only)"):
...             if not st.session_state["draft_ids"]:
...                 st.warning("No drafts recorded in this session. Create drafts first.")
...             elif not send_confirm:
...                 st.warning("Please tick 'Confirm send' before sending.")
...             else:
...                 send_drafts(gmail_service, st.session_state["draft_ids"])
...                 st.success(f"Sent {len(st.session_state['draft_ids'])} draft(s).")
...                 st.session_state["draft_ids"].clear()
... 
...     with col3:
...         if st.button("🧹 Clear session draft IDs"):
...             st.session_state["draft_ids"].clear()
...             st.info("Cleared session draft IDs (does not delete Gmail drafts).")
... 
...     st.caption(
...         "Tip: Keep this URL private. For production use, add an opt-out footer, "
...         "a per-user login, and store per-user tokens in a DB."
...     )
... 
... if __name__ == "__main__":
...     main()
