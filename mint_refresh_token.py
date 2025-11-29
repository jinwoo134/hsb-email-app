from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json", SCOPES
    )
    creds = flow.run_local_server(
        port=0,
        access_type="offline",       # required for refresh token
        include_granted_scopes="true",
        prompt="consent"             # forces refresh token on every run
    )

    # Write token.json
    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    # Optional: print values for debugging
    print("client_id:", flow.client_config["client_id"])
    print("client_secret:", flow.client_config["client_secret"])
    print("refresh_token:", creds.refresh_token)

if __name__ == "__main__":
    main()
