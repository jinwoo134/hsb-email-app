import base64
import mimetypes
from typing import Iterable, List, Optional

import pandas as pd
import streamlit as st
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="📧 Personalized Email Draft App", layout="centered")

# ---------- CONFIG ----------
SCOPES = [
    # Draft creation
    "https://www.googleapis.com/auth/gmail.compose",
    # Sending drafts/messages
    "https://www.googleapis.com/auth/gmail.send",
    # Read Google Sheets
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

DEFAULT_SHEET_ID = "1TerALNKo3SBzfbsp0qxPiDs-b7NG-Sn1Pcp7I24gCPY"  # fallback if not in secrets
SHEET_ID = st.secrets.get("SHEET_ID", DEFAULT_SHEET_ID)
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "")

# Track only this session's drafts (unchanged; safe to leave even if unused for sending)
if "draft_ids" not in st.session_state:
    st.session_state["draft_ids"] = []

# ---------- SECRET VALIDATION ----------
REQUIRED_SECRETS = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"]
missing = [k for k in REQUIRED_SECRETS if k not in st.secrets]
if missing:
    st.error(
        "Missing Streamlit secrets: " + ", ".join(missing) +
        ". Add them in the app’s Settings → Secrets, then restart."
    )
    st.stop()

# ---------- AUTH (secrets-based; one refresh token for both APIs) ----------
def creds_from_secrets(scopes: List[str]) -> Credentials:
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
def load_sheet_data(sheet_service, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """
    Expects columns: 이름, 전자 메일 주소, 직함, 친구, plus deal-type columns.
    """
    result = sheet_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=sheet_name
    ).execute()
    data = result.get("values", [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data[1:], columns=data[0])
    df.columns = df.columns.str.strip()
    return df

# ---------- UTIL ----------
def trim_first_syllable(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return name[1:] if len(name) > 1 else name

def has_hangul_syllable(ch: str) -> bool:
    # Hangul syllables range
    return 0xAC00 <= ord(ch) <= 0xD7A3

def subject_for_row(friend_filter: str, remove_suffix: bool, subject_input: str,
                    name: str, position: str) -> str:
    if remove_suffix:
        return subject_input

    if friend_filter == "친구":
        trimmed = trim_first_syllable(name or "")
        if trimmed and has_hangul_syllable(trimmed[-1]):
            # 받침 check: (codepoint-0xAC00) % 28 -> 0 means no 받침 → '야', else '아'
            suffix = "아" if ((ord(trimmed[-1]) - 0xAC00) % 28) else "야"
        else:
            # Fallback for non-Hangul or empty
            suffix = ""
        return f"{trimmed}{suffix}, {subject_input}".strip(", ")
    else:
        return f"{(position or '').strip()}님, {subject_input}" if position else subject_input

def build_mime_with_attachments(
    to_: str, subject_: str, body_: str, files: Optional[Iterable]
):
    msg = MIMEMultipart()
    msg["to"] = to_
    msg["subject"] = subject_
    msg.attach(MIMEText(body_ or "", "plain"))

    if files:
        for f in files:
            # Streamlit UploadedFile can be re-read if we seek(0)
            try:
                f.seek(0)
                content = f.read()
            except Exception:
                # If the object was already consumed, skip gracefully
                continue

            filename = getattr(f, "name", None) or "attachment"
            content_type, encoding = mimetypes.guess_type(filename)
            if content_type is None or encoding is not None:
                content_type = "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)

            part = MIMEBase(main_type, sub_type)
            part.set_payload(content)
            encoders.encode_base64(part)
            # These headers are important to prevent "attach.txt" behavior
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            part.add_header("Content-Type", f'{main_type}/{sub_type}; name="{filename}"')
            msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"raw": raw}

def create_draft(gmail_service, to: str, subject: str, body: str, files=None) -> Optional[str]:
    raw = build_mime_with_attachments(to, subject, body, files)
    draft = gmail_service.users().drafts().create(userId="me", body={"message": raw}).execute()
    return draft.get("id")

def send_drafts(gmail_service, draft_ids: List[str]) -> None:
    for did in draft_ids:
        gmail_service.users().drafts().send(userId="me", body={"id": did}).execute()

# ---- NEW: minimal helper to fetch ALL draft IDs in the mailbox ----
def list_all_draft_ids(gmail_service) -> List[str]:
    ids: List[str] = []
    page_token = None
    while True:
        resp = gmail_service.users().drafts().list(userId="me", pageToken=page_token).execute()
        for d in resp.get("drafts", []):
            if "id" in d:
                ids.append(d["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids

# ---------- APP ----------
def main():
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

    # Validate required columns
    required = {"이름", "전자 메일 주소", "직함", "친구"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        st.error(f"Missing required columns in sheet: {', '.join(missing_cols)}")
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

    # Filtering: friends vs non-friends
    if friend_filter == "친구":
        filtered_df = df[df["친구"].astype(str).str.strip().ne("")]
    else:
        filtered_df = df[df["친구"].isna() | df["친구"].astype(str).str.strip().eq("")]

    # Deal filter (only rows where that column has a non-empty value)
    if deal_filter in filtered_df.columns:
        filtered_df = filtered_df[filtered_df[deal_filter].astype(str).str.strip().ne("")].copy()

    # Build preview rows
    preview_rows = []
    for _, row in filtered_df.iterrows():
        name = str(row.get("이름", "")).strip()
        email = str(row.get("전자 메일 주소", "")).strip()
        position = str(row.get("직함", "")).strip()
        if not email:
            continue

        subject = subject_for_row(friend_filter, remove_suffix, subject_input, name, position)
        preview_rows.append({"이메일": email, "제목": subject, "본문": body_input})

    preview_df = pd.DataFrame(preview_rows)
    st.subheader("미리보기")
    st.dataframe(preview_df, use_container_width=True)

    # Informational: show total drafts currently in Gmail (useful before sending ALL)
    try:
        total_drafts_count = len(list_all_draft_ids(gmail_service))
        st.info(f"현재 Gmail 초안 수: {total_drafts_count}개 (이 앱 외에 만든 초안도 포함됩니다)")
    except Exception:
        pass

    # Actions
    col1, col2 = st.columns([1, 1])

    with col1:
        if st.button("💾 Save as Drafts"):
            st.session_state["draft_ids"].clear()
            for _, row in preview_df.iterrows():
                did = create_draft(
                    gmail_service,
                    to=row["이메일"],
                    subject=row["제목"],
                    body=row["본문"],
                    files=file_inputs,
                )
                if did:
                    st.session_state["draft_ids"].append(did)
            st.success(f"Drafts created: {len(st.session_state['draft_ids'])}")

    with col2:
        send_confirm = st.checkbox("✅ Confirm send ALL drafts in Gmail")
        if st.button("📤 Send ALL Gmail Drafts"):
            all_ids = list_all_draft_ids(gmail_service)
            if not all_ids:
                st.warning("No drafts found in Gmail.")
            elif not send_confirm:
                st.warning("Please tick 'Confirm send ALL drafts in Gmail' before sending.")
            else:
                send_drafts(gmail_service, all_ids)
                st.success(f"Sent {len(all_ids)} draft(s).")

    st.caption(
        "Tip: Keep this URL private. For production use, add an opt-out footer, "
        "a per-user login, and store per-user tokens in a DB."
    )

if __name__ == "__main__":
    main()
