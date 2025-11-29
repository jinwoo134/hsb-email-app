"""
Enhanced Email CRM Tracker - Streamlit Page
Features:
1. Engagement metrics (sent/received, response rate, last contact)
2. AI sentiment analysis & opportunity detection
3. Smart follow-up recommendations
"""

import json
import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import anthropic
import base64

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="📊 CRM Tracker", page_icon="📊", layout="wide")

# ---------- CONFIG ----------
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Local storage file for tracking data
TRACKER_FILE = Path("email_crm_data.json")


# ---------- SECRET VALIDATION ----------
REQUIRED_SECRETS = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"]
missing = [k for k in REQUIRED_SECRETS if k not in st.secrets]
if missing:
    st.error(
        "Missing Streamlit secrets: " + ", ".join(missing) +
        ". Add them in the app's Settings → Secrets, then restart."
    )
    st.stop()


# ---------- AUTH ----------
def get_gmail_service():
    """Create Gmail service from Streamlit secrets."""
    creds = Credentials(
        token=None,
        refresh_token=st.secrets["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["GOOGLE_CLIENT_ID"],
        client_secret=st.secrets["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def get_anthropic_client():
    """Get Anthropic client if API key is available."""
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


# ---------- DATA PERSISTENCE ----------
def load_tracker_data() -> Dict:
    """Load existing tracker data from file."""
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return {
        "last_scan_date": None,
        "contacts": {},  # email -> contact data
        "total_threads_scanned": 0,
    }


def save_tracker_data(data: Dict):
    """Save tracker data to file."""
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------- EMAIL PARSING ----------
def get_email_from_headers(headers: List[dict]) -> str:
    """Extract email address from message headers."""
    for header in headers:
        if header["name"].lower() == "from":
            value = header["value"]
            if "<" in value and ">" in value:
                return value.split("<")[1].split(">")[0].strip().lower()
            return value.strip().lower()
    return ""


def get_date_from_headers(headers: List[dict]) -> Optional[str]:
    """Extract date from message headers."""
    for header in headers:
        if header["name"].lower() == "date":
            return header["value"]
    return None


def get_message_body(message: dict) -> str:
    """Extract text body from message."""
    try:
        payload = message.get("payload", {})

        # Check for plain text part
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        # Single part message
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        return ""
    except Exception as e:
        return ""


def get_thread_messages(gmail_service, thread_id: str) -> List[dict]:
    """Get all messages in a thread with full content."""
    try:
        thread = gmail_service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full"  # Changed to full to get message bodies
        ).execute()
        return thread.get("messages", [])
    except Exception as e:
        st.error(f"Error fetching thread {thread_id}: {e}")
        return []


# ---------- ENHANCED EMAIL SCANNING ----------
def scan_emails_enhanced(
    gmail_service,
    after_date: str = None,
    my_email: str = None,
    progress_bar=None,
    ai_client=None
) -> Dict[str, dict]:
    """
    Enhanced scan that tracks:
    - Emails sent TO contact
    - Emails received FROM contact
    - Last contact date
    - Response rate
    - Recent email content for AI analysis
    """
    contacts = defaultdict(lambda: {
        "emails_sent_to": 0,
        "emails_received_from": 0,
        "last_contact_date": None,
        "last_email_content": "",
        "first_contact_date": None,
        "threads": []
    })

    # Build query
    query = f"after:{after_date}" if after_date else ""

    page_token = None
    thread_count = 0
    total_threads = 0

    # Get total count for progress bar
    if progress_bar:
        try:
            initial_response = gmail_service.users().threads().list(
                userId="me",
                q=query,
                maxResults=1
            ).execute()
            total_threads = initial_response.get("resultSizeEstimate", 0)
        except:
            total_threads = 0

    while True:
        try:
            response = gmail_service.users().threads().list(
                userId="me",
                q=query,
                pageToken=page_token,
                maxResults=100
            ).execute()

            threads = response.get("threads", [])

            for thread in threads:
                thread_count += 1

                if progress_bar and total_threads > 0:
                    progress = min(thread_count / total_threads, 1.0)
                    progress_bar.progress(progress, text=f"Scanning thread {thread_count} of ~{total_threads}")

                messages = get_thread_messages(gmail_service, thread["id"])

                for message in messages:
                    headers = message.get("payload", {}).get("headers", [])
                    sender_email = get_email_from_headers(headers)
                    date_str = get_date_from_headers(headers)

                    # Skip your own email
                    if my_email and sender_email == my_email.lower():
                        continue

                    if sender_email:
                        # Update contact data
                        contact = contacts[sender_email]
                        contact["emails_received_from"] += 1

                        # Update dates
                        if date_str:
                            if not contact["last_contact_date"] or date_str > contact["last_contact_date"]:
                                contact["last_contact_date"] = date_str
                            if not contact["first_contact_date"] or date_str < contact["first_contact_date"]:
                                contact["first_contact_date"] = date_str

                        # Store recent email content for AI analysis (last 3 emails)
                        body = get_message_body(message)
                        if body and len(body) > 50:  # Only store substantial emails
                            if "recent_emails" not in contact:
                                contact["recent_emails"] = []
                            contact["recent_emails"].append({
                                "date": date_str,
                                "body": body[:2000]  # Limit to 2000 chars per email
                            })
                            # Keep only last 3 emails
                            contact["recent_emails"] = sorted(
                                contact["recent_emails"],
                                key=lambda x: x["date"],
                                reverse=True
                            )[:3]

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        except Exception as e:
            st.error(f"Error listing threads: {e}")
            break

    return dict(contacts), thread_count


# ---------- AI ANALYSIS ----------
def analyze_contact_with_ai(contact_email: str, contact_data: dict, ai_client) -> dict:
    """
    Use Claude to analyze contact engagement and extract insights.
    """
    if not ai_client or "recent_emails" not in contact_data:
        return {}

    recent_emails = contact_data.get("recent_emails", [])
    if not recent_emails:
        return {}

    # Build email context
    email_context = "\n\n---\n\n".join([
        f"Date: {email['date']}\n{email['body'][:1000]}"
        for email in recent_emails[:3]
    ])

    prompt = f"""Analyze these recent emails from {contact_email}:

{email_context}

Provide a JSON response with:
1. sentiment: positive/neutral/negative/urgent
2. engagement_level: hot/warm/cold (based on tone and content)
3. buying_signals: list of any phrases indicating interest, pricing questions, next steps
4. key_topics: list of main topics discussed
5. follow_up_recommended: true/false
6. follow_up_reason: why follow-up is needed (if applicable)
7. priority_score: 1-10 (how important is this contact)

Return ONLY valid JSON, no other text."""

    try:
        message = ai_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.content[0].text
        # Extract JSON from response
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {}
    except Exception as e:
        st.error(f"AI analysis error for {contact_email}: {e}")
        return {}


def generate_follow_up_draft(contact_email: str, contact_data: dict, ai_analysis: dict, ai_client) -> str:
    """Generate a personalized follow-up email draft using AI."""
    if not ai_client:
        return ""

    recent_emails = contact_data.get("recent_emails", [])
    if not recent_emails:
        return ""

    email_context = recent_emails[0]["body"][:1000] if recent_emails else ""

    prompt = f"""Based on this recent email from {contact_email}:

{email_context}

Analysis: {json.dumps(ai_analysis, indent=2)}

Write a brief, professional follow-up email (2-3 sentences) that:
1. References their last message
2. Moves the conversation forward
3. Includes a clear call-to-action

Return ONLY the email body, no subject line or greetings."""

    try:
        message = ai_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text.strip()
    except Exception as e:
        return f"Error generating draft: {e}"


# ---------- MAIN APP ----------
def main():
    st.title("📊 Email CRM Tracker")
    st.caption("AI-powered contact management and engagement tracking")

    # Check for Anthropic API key
    ai_client = get_anthropic_client()
    if not ai_client:
        st.warning("⚠️ **ANTHROPIC_API_KEY** not found in secrets. AI features will be disabled. Add it to enable sentiment analysis and follow-up suggestions.")

    # Load existing data
    tracker_data = load_tracker_data()

    # Display current stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        last_scan = tracker_data.get("last_scan_date")
        st.metric("Last Scan", last_scan if last_scan else "Never")

    with col2:
        total_threads = tracker_data.get("total_threads_scanned", 0)
        st.metric("Threads Scanned", f"{total_threads:,}")

    with col3:
        total_contacts = len(tracker_data.get("contacts", {}))
        st.metric("Total Contacts", total_contacts)

    with col4:
        # Count contacts needing follow-up
        needs_followup = sum(
            1 for c in tracker_data.get("contacts", {}).values()
            if c.get("ai_analysis", {}).get("follow_up_recommended", False)
        )
        st.metric("Needs Follow-up", needs_followup)

    st.divider()

    # Scan controls
    st.subheader("🔍 Scan Options")

    col_a, col_b, col_c = st.columns([2, 1, 1])

    with col_a:
        scan_mode = st.radio(
            "Scan Mode",
            ["Incremental (since last scan)", "Full Rescan (all time)", "Custom Date Range"],
            help="Incremental is fastest and recommended for regular updates"
        )

    with col_b:
        if scan_mode == "Custom Date Range":
            days_back = st.number_input("Days back", min_value=1, max_value=365, value=30)

    with col_c:
        enable_ai = st.checkbox("Enable AI Analysis", value=bool(ai_client), disabled=not ai_client)

    # Scan button
    if st.button("🚀 Scan Emails", type="primary", use_container_width=True):
        try:
            gmail_service = get_gmail_service()
            my_email = st.secrets.get("SENDER_EMAIL", "").lower()

            # Determine scan date
            if scan_mode == "Incremental (since last scan)":
                if tracker_data["last_scan_date"]:
                    after_date = tracker_data["last_scan_date"]
                    st.info(f"📅 Scanning emails since {after_date}")
                else:
                    after_date = (datetime.now() - timedelta(days=30)).strftime("%Y/%m/%d")
                    st.info(f"📅 First scan! Scanning last 30 days from {after_date}")

            elif scan_mode == "Full Rescan (all time)":
                after_date = None
                st.warning("⚠️ Full rescan will take longer. This will replace all existing data.")
                tracker_data["contacts"] = {}
                tracker_data["total_threads_scanned"] = 0

            else:  # Custom
                after_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
                st.info(f"📅 Scanning emails since {after_date}")
                tracker_data["contacts"] = {}
                tracker_data["total_threads_scanned"] = 0

            # Create progress bar
            progress_bar = st.progress(0, text="Starting scan...")

            # Scan emails
            with st.spinner("Scanning emails..."):
                new_contacts, threads_scanned = scan_emails_enhanced(
                    gmail_service,
                    after_date=after_date,
                    my_email=my_email,
                    progress_bar=progress_bar,
                    ai_client=ai_client if enable_ai else None
                )

            progress_bar.empty()

            # Merge contacts
            existing_contacts = tracker_data.get("contacts", {})

            if scan_mode == "Incremental (since last scan)":
                # Merge new data with existing
                for email, data in new_contacts.items():
                    if email in existing_contacts:
                        existing_contacts[email]["emails_received_from"] += data["emails_received_from"]
                        if data.get("last_contact_date"):
                            if not existing_contacts[email].get("last_contact_date") or \
                               data["last_contact_date"] > existing_contacts[email]["last_contact_date"]:
                                existing_contacts[email]["last_contact_date"] = data["last_contact_date"]
                        # Merge recent emails
                        if "recent_emails" in data:
                            if "recent_emails" not in existing_contacts[email]:
                                existing_contacts[email]["recent_emails"] = []
                            existing_contacts[email]["recent_emails"].extend(data["recent_emails"])
                            existing_contacts[email]["recent_emails"] = sorted(
                                existing_contacts[email]["recent_emails"],
                                key=lambda x: x["date"],
                                reverse=True
                            )[:3]
                    else:
                        existing_contacts[email] = data
            else:
                existing_contacts = new_contacts

            # AI Analysis Phase
            if enable_ai and ai_client:
                st.info("🤖 Running AI analysis on contacts...")
                ai_progress = st.progress(0, text="Analyzing contacts...")

                contacts_to_analyze = list(existing_contacts.items())
                for idx, (email, data) in enumerate(contacts_to_analyze):
                    if "recent_emails" in data and data["recent_emails"]:
                        ai_analysis = analyze_contact_with_ai(email, data, ai_client)
                        existing_contacts[email]["ai_analysis"] = ai_analysis

                    ai_progress.progress((idx + 1) / len(contacts_to_analyze),
                                       text=f"Analyzing {idx + 1}/{len(contacts_to_analyze)}")

                ai_progress.empty()

            # Update tracker data
            tracker_data["contacts"] = existing_contacts
            tracker_data["last_scan_date"] = datetime.now().strftime("%Y/%m/%d")
            tracker_data["total_threads_scanned"] += threads_scanned

            # Save to file
            save_tracker_data(tracker_data)

            st.success(f"✅ Scan complete! Processed {threads_scanned} threads, tracked {len(existing_contacts)} contacts.")
            st.rerun()

        except Exception as e:
            st.error(f"Error during scan: {e}")
            st.exception(e)

    st.divider()

    # Display results in tabs
    tab1, tab2, tab3 = st.tabs(["📈 Engagement Metrics", "🔥 Hot Leads", "💬 Follow-up Queue"])

    contacts = tracker_data.get("contacts", {})

    # TAB 1: Engagement Metrics
    with tab1:
        st.subheader("📈 Contact Engagement Overview")

        if not contacts:
            st.info("No data yet. Click 'Scan Emails' to start tracking!")
        else:
            # Prepare data for display
            import pandas as pd

            contact_list = []
            for email, data in contacts.items():
                received = data.get("emails_received_from", 0)
                sent = data.get("emails_sent_to", 0)
                response_rate = (received / sent * 100) if sent > 0 else 0

                contact_list.append({
                    "Email": email,
                    "Received": received,
                    "Sent": sent,
                    "Response Rate": f"{response_rate:.0f}%",
                    "Last Contact": data.get("last_contact_date", "Unknown")[:10] if data.get("last_contact_date") else "Unknown",
                    "Engagement": data.get("ai_analysis", {}).get("engagement_level", "N/A"),
                    "Priority": data.get("ai_analysis", {}).get("priority_score", 0)
                })

            df = pd.DataFrame(contact_list)
            df = df.sort_values("Received", ascending=False)
            df.index = range(1, len(df) + 1)

            # Filter options
            col_x, col_y = st.columns([1, 3])
            with col_x:
                show_top_n = st.selectbox("Show top", [10, 20, 50, 100, "All"], index=1)
                if show_top_n != "All":
                    df = df.head(show_top_n)

            st.dataframe(df, use_container_width=True, height=600)

            # Download button
            csv = df.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"crm_contacts_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True
            )

    # TAB 2: Hot Leads
    with tab2:
        st.subheader("🔥 Hot Leads & Opportunities")

        hot_leads = [
            (email, data) for email, data in contacts.items()
            if data.get("ai_analysis", {}).get("engagement_level") in ["hot", "warm"]
            or data.get("ai_analysis", {}).get("priority_score", 0) >= 7
        ]

        if not hot_leads:
            st.info("No hot leads detected yet. Run a scan with AI enabled to identify opportunities.")
        else:
            for email, data in sorted(hot_leads, key=lambda x: x[1].get("ai_analysis", {}).get("priority_score", 0), reverse=True):
                ai_analysis = data.get("ai_analysis", {})

                with st.expander(f"🔥 {email} - Priority: {ai_analysis.get('priority_score', 0)}/10"):
                    col1, col2 = st.columns([1, 1])

                    with col1:
                        st.metric("Engagement", ai_analysis.get("engagement_level", "N/A").upper())
                        st.metric("Sentiment", ai_analysis.get("sentiment", "N/A").upper())
                        st.metric("Emails Received", data.get("emails_received_from", 0))

                    with col2:
                        buying_signals = ai_analysis.get("buying_signals", [])
                        if buying_signals:
                            st.write("**🎯 Buying Signals:**")
                            for signal in buying_signals:
                                st.write(f"- {signal}")

                        topics = ai_analysis.get("key_topics", [])
                        if topics:
                            st.write("**📌 Topics:**")
                            st.write(", ".join(topics))

    # TAB 3: Follow-up Queue
    with tab3:
        st.subheader("💬 Smart Follow-up Recommendations")

        follow_ups = [
            (email, data) for email, data in contacts.items()
            if data.get("ai_analysis", {}).get("follow_up_recommended", False)
        ]

        if not follow_ups:
            st.info("No follow-ups recommended. Run a scan with AI enabled to get suggestions.")
        else:
            for email, data in follow_ups:
                ai_analysis = data.get("ai_analysis", {})

                with st.expander(f"📧 {email} - {ai_analysis.get('follow_up_reason', 'Follow-up needed')}"):
                    st.write(f"**Last Contact:** {data.get('last_contact_date', 'Unknown')[:10]}")
                    st.write(f"**Priority:** {ai_analysis.get('priority_score', 0)}/10")

                    if st.button(f"✨ Generate Follow-up Draft", key=f"draft_{email}"):
                        with st.spinner("Generating personalized draft..."):
                            draft = generate_follow_up_draft(email, data, ai_analysis, ai_client)
                            st.text_area("Suggested Email:", draft, height=150, key=f"text_{email}")

    # Reset button in sidebar
    with st.sidebar:
        st.subheader("⚙️ Settings")

        if ai_client:
            st.success("✅ AI Features Enabled")
        else:
            st.warning("⚠️ AI Features Disabled")
            st.caption("Add ANTHROPIC_API_KEY to secrets to enable")

        st.divider()

        if st.button("🗑️ Reset All Data", type="secondary"):
            if TRACKER_FILE.exists():
                TRACKER_FILE.unlink()
            st.success("Data reset!")
            st.rerun()

        st.divider()
        st.caption("💡 **Tips:**\n- Use incremental scans for daily updates\n- Enable AI for lead scoring\n- Check follow-up queue regularly")


if __name__ == "__main__":
    main()
