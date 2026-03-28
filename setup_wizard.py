"""Setup wizard for configuring Google Sheets credentials."""

import base64
import json
from pathlib import Path


ENV_FILE = Path(".env")
REQUIRED_FIELDS = ["type", "project_id", "private_key", "client_email"]


def _load_env() -> dict[str, str]:
    """Load existing .env file into a dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _save_env(env: dict[str, str]):
    """Write env dict back to .env, preserving comments and structure."""
    lines: list[str] = []
    existing_keys: set[str] = set()

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    existing_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)

    # Append new keys not already in the file
    for key, value in env.items():
        if key not in existing_keys:
            lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n")


def _validate_service_account_json(creds: dict) -> list[str]:
    """Return list of missing required fields."""
    return [f for f in REQUIRED_FIELDS if f not in creds]


def _read_credentials_file(path: Path) -> dict:
    """Read and parse a service account JSON file."""
    text = path.read_text()
    return json.loads(text)


def setup_google_credentials():
    """Interactive setup for Google Sheets credentials."""
    print("\n=== Google Sheets Credentials Setup ===\n")
    print("You need a Google Cloud Service Account JSON key file.")
    print("If you don't have one yet:")
    print("  1. Go to https://console.cloud.google.com")
    print("  2. Enable Google Sheets API + Google Drive API")
    print("  3. Create a Service Account, download JSON key")
    print()

    # Ask for credentials file path
    while True:
        path_str = input("Path to service account JSON key file: ").strip()
        if not path_str:
            print("  Skipping Google credentials setup.")
            return

        creds_path = Path(path_str).expanduser()
        if not creds_path.exists():
            print(f"  File not found: {creds_path}")
            continue

        try:
            creds = _read_credentials_file(creds_path)
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON: {e}")
            continue

        missing = _validate_service_account_json(creds)
        if missing:
            print(f"  Missing fields: {', '.join(missing)}")
            print("  This doesn't look like a valid service account key.")
            continue

        break

    client_email = creds["client_email"]
    print(f"\n  Service account email: {client_email}")
    print("  Make sure to share your Google Sheet with this email (Editor access).")

    # Encode to base64
    creds_json = json.dumps(creds, separators=(",", ":"))
    creds_b64 = base64.b64encode(creds_json.encode()).decode()

    # Ask for spreadsheet ID
    print()
    spreadsheet_id = input(
        "Google Spreadsheet ID (from the URL, or press Enter to skip): "
    ).strip()

    # Update .env
    env = _load_env()
    env["GOOGLE_CREDENTIALS_BASE64"] = creds_b64
    # Remove old formats if present
    env.pop("GOOGLE_CREDENTIALS_JSON", None)
    env.pop("GOOGLE_CREDENTIALS_FILE", None)

    if spreadsheet_id:
        env["SPREADSHEET_ID"] = spreadsheet_id

    _save_env(env)
    print(f"\n  Saved GOOGLE_CREDENTIALS_BASE64 to {ENV_FILE}")
    if spreadsheet_id:
        print(f"  Saved SPREADSHEET_ID={spreadsheet_id}")

    # Quick verification
    print("\n  Verifying credentials...")
    try:
        from google.oauth2.service_account import Credentials

        import gspread

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(creds, scopes=scopes)
        client = gspread.authorize(credentials)

        if spreadsheet_id:
            sheet = client.open_by_key(spreadsheet_id)
            print(f"  Successfully connected to sheet: {sheet.title}")
        else:
            # Just verify auth works by listing spreadsheets
            client.list_spreadsheet_files(title=None)
            print("  Auth successful (no spreadsheet ID to verify).")

        print("  Google Sheets setup complete!")

    except Exception as e:
        print(f"  Verification failed: {e}")
        print("  Credentials were saved — check the error and try again.")
        print("  Common issues:")
        print("    - Sheet not shared with the service account email")
        print("    - Google Sheets/Drive API not enabled in the project")


def main():
    print("=== Meetup Bot Setup Wizard ===")
    print()

    env = _load_env()

    # Show current state
    has_google = bool(
        env.get("GOOGLE_CREDENTIALS_BASE64")
        or env.get("GOOGLE_CREDENTIALS_JSON")
        or env.get("GOOGLE_CREDENTIALS_FILE")
    )
    has_sheet_id = bool(env.get("SPREADSHEET_ID"))

    print("Current status:")
    print(f"  Google credentials: {'configured' if has_google else 'MISSING'}")
    print(f"  Spreadsheet ID:     {'configured' if has_sheet_id else 'MISSING'}")
    print()

    setup_google_credentials()
    print("\nDone!")


if __name__ == "__main__":
    main()
