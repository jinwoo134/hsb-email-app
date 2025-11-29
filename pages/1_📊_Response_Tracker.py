"""
Email Response Tracker - Streamlit Page
Tracks email responses incrementally with persistent storage.
"""

import json
import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="📊 Response Tracker", page_icon="📊", layout="wide")

# ---------- CONFIG ----------
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Local storage file for tracking data
TRACKER_FILE = Path("email_response_data.json")


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


# ---------- DATA PERSISTENCE ----------
def load_tracker_data() -> Dict:
    """Load existing tracker data from file."""
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return {
        "last_scan_date": None,
        "response_counts": {},
        "total_threads_scanned": 0,
    }


def save_tracker_data(data: Dict):
    """Save tracker data to file."""
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------- EMAIL SCANNING ----------
def get_email_from_headers(headers: List[dict]) -> str:
    """Extract email address from message headers."""
    for header in headers:
        if header["name"].lower() == "from":
            value = header["value"]
            if "<" in value and ">" in value:
                return value.split("<")[1].split(">")[0].strip().lower()
            return value.strip().lower()
    return ""


def get_thread_messages(gmail_service, thread_id: str) -> List[dict]:
    """Get all messages in a thread."""
    try:
        thread = gmail_service.users().threads().get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"]
        ).execute()
        return thread.get("messages", [])
    except Exception as e:
        st.error(f"Error fetching thread {thread_id}: {e}")
        return []


def scan_emails_since(
    gmail_service,
    after_date: str = None,
    my_email: str = None,
    progress_bar=None
) -> tuple:
    """
    Scan emails and count responses since a specific date.

    Args:
        gmail_service: Gmail API service
        after_date: Date string in format "YYYY/MM/DD" (None = all time)
        my_email: Your email to exclude from counts
        progress_bar: Streamlit progress bar to update

    Returns:
        Tuple of (response_counts_dict, thread_count)
    """
    response_counts = defaultdict(int)

    # Build query
    query = f"after:{after_date}" if after_date else ""

    page_token = None
    thread_count = 0
    total_threads = 0

    # First, get total count for progress bar
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
                maxResults=100  # Process in batches
            ).execute()

            threads = response.get("threads", [])

            for idx, thread in enumerate(threads):
                thread_count += 1

                # Update progress
                if progress_bar and total_threads > 0:
                    progress = min(thread_count / total_threads, 1.0)
                    progress_bar.progress(progress, text=f"Scanning thread {thread_count} of ~{total_threads}")

                messages = get_thread_messages(gmail_service, thread["id"])

                if len(messages) < 2:
                    continue

                # Count responses (skip first message which is original)
                for message in messages[1:]:
                    headers = message.get("payload", {}).get("headers", [])
                    sender_email = get_email_from_headers(headers)

                    # Skip your own email
                    if my_email and sender_email == my_email.lower():
                        continue

                    if sender_email:
                        response_counts[sender_email] += 1

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        except Exception as e:
            st.error(f"Error listing threads: {e}")
            break

    return dict(response_counts), thread_count


# ---------- MAIN APP ----------
def main():
    st.title("📊 Email Response Tracker")
    st.caption("Track who responds to your emails and how often")

    # Load existing data
    tracker_data = load_tracker_data()

    # Display current stats
    col1, col2, col3 = st.columns(3)
    with col1:
        last_scan = tracker_data.get("last_scan_date")
        if last_scan:
            st.metric("Last Scan", last_scan)
        else:
            st.metric("Last Scan", "Never")

    with col2:
        total_threads = tracker_data.get("total_threads_scanned", 0)
        st.metric("Total Threads Scanned", f"{total_threads:,}")

    with col3:
        unique_responders = len(tracker_data.get("response_counts", {}))
        st.metric("Unique Responders", unique_responders)

    st.divider()

    # Scan controls
    st.subheader("🔍 Scan Options")

    col_a, col_b = st.columns([2, 1])

    with col_a:
        scan_mode = st.radio(
            "Scan Mode",
            ["Incremental (since last scan)", "Full Rescan (all time)", "Custom Date Range"],
            help="Incremental is fastest and recommended for regular updates"
        )

    with col_b:
        if scan_mode == "Custom Date Range":
            days_back = st.number_input("Days back to scan", min_value=1, max_value=365, value=30)

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
                    # First scan ever - go back 30 days
                    after_date = (datetime.now() - timedelta(days=30)).strftime("%Y/%m/%d")
                    st.info(f"📅 First scan! Scanning last 30 days from {after_date}")

            elif scan_mode == "Full Rescan (all time)":
                after_date = None
                st.warning("⚠️ Full rescan will take longer. This will replace all existing data.")
                tracker_data["response_counts"] = {}
                tracker_data["total_threads_scanned"] = 0

            else:  # Custom date range
                after_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
                st.info(f"📅 Scanning emails since {after_date}")
                tracker_data["response_counts"] = {}
                tracker_data["total_threads_scanned"] = 0

            # Create progress bar
            progress_bar = st.progress(0, text="Starting scan...")

            # Scan emails
            with st.spinner("Scanning emails..."):
                new_counts, threads_scanned = scan_emails_since(
                    gmail_service,
                    after_date=after_date,
                    my_email=my_email,
                    progress_bar=progress_bar
                )

            # Clear progress bar
            progress_bar.empty()

            # Merge counts (add new to existing)
            existing_counts = tracker_data.get("response_counts", {})

            if scan_mode == "Incremental (since last scan)":
                # Add new counts to existing
                for email, count in new_counts.items():
                    existing_counts[email] = existing_counts.get(email, 0) + count
            else:
                # Replace with new counts
                existing_counts = new_counts

            # Update tracker data
            tracker_data["response_counts"] = existing_counts
            tracker_data["last_scan_date"] = datetime.now().strftime("%Y/%m/%d")
            tracker_data["total_threads_scanned"] += threads_scanned

            # Save to file
            save_tracker_data(tracker_data)

            st.success(f"✅ Scan complete! Processed {threads_scanned} threads.")
            st.rerun()

        except Exception as e:
            st.error(f"Error during scan: {e}")
            st.exception(e)

    st.divider()

    # Display results
    st.subheader("📈 Response Statistics")

    response_counts = tracker_data.get("response_counts", {})

    if not response_counts:
        st.info("No data yet. Click 'Scan Emails' to start tracking!")
    else:
        # Sort by count descending
        sorted_counts = sorted(response_counts.items(), key=lambda x: x[1], reverse=True)

        # Display options
        col_x, col_y = st.columns([1, 3])
        with col_x:
            show_top_n = st.selectbox("Show top", [10, 20, 50, 100, "All"], index=1)
            if show_top_n != "All":
                sorted_counts = sorted_counts[:show_top_n]

        # Create table
        import pandas as pd
        df = pd.DataFrame(sorted_counts, columns=["Email", "Response Count"])
        df.index = range(1, len(df) + 1)

        st.dataframe(df, use_container_width=True, height=600)

        # Download button
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Download CSV",
            data=csv,
            file_name=f"email_responses_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )

    # Reset button (in sidebar)
    with st.sidebar:
        st.subheader("⚙️ Settings")
        if st.button("🗑️ Reset All Data", type="secondary"):
            if TRACKER_FILE.exists():
                TRACKER_FILE.unlink()
            st.success("Data reset!")
            st.rerun()

        st.divider()
        st.caption("💡 **Tip:** Use incremental scans for best performance. Full rescans are only needed if you want to rebuild from scratch.")


if __name__ == "__main__":
    main()
