"""
Google Drive / Docs / Sheets helper for Cowork sessions.

This module auto-discovers the user's OAuth credentials and the bundled
Google API client libraries, so it works from any Cowork session without
hardcoded session paths. Import it and call the functions below.

It expects a "google-workspace" folder (the one set up in the original auth
session) to be connected/mounted, containing:
    google_token.json          OAuth access + refresh token
    google_client_secret.json  Google Cloud app credentials (installed app)
    glibs/                      google-api-python-client + deps

Typical usage from bash:

    import google_helper as g
    g.search_files("name contains 'budget'")
    g.read_doc("DOC_ID")
    g.read_sheet("SHEET_ID", "Sheet1!A1:Z100")
"""
import os
import sys
import glob
import json
import io
import base64
from email.message import EmailMessage


# ── Credential / library discovery ──────────────────────────────────────────

def _find_base():
    """Locate the google-workspace folder containing the token + secrets.

    Searches the connected-folder mounts and the outputs scratchpad. Returns
    the directory holding google_token.json. Raises if nothing is found so the
    caller gets a clear message instead of a confusing auth error.
    """
    # Allow an explicit override.
    env = os.environ.get("GOOGLE_WORKSPACE_DIR")
    if env and os.path.exists(os.path.join(env, "google_token.json")):
        return env

    patterns = [
        "/sessions/*/mnt/**/google-workspace/google_token.json",
        "/sessions/*/mnt/**/google_token.json",
        os.path.expanduser("~/Dokumentumok/Claude/**/google_token.json"),
        os.path.expanduser("~/**/google-workspace/google_token.json"),
    ]
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits:
            return os.path.dirname(hits[0])
    raise FileNotFoundError(
        "google_token.json not found. Connect the folder that holds the Google "
        "Workspace credentials (e.g. ~/Dokumentumok/Claude/google-workspace) and "
        "retry, or set GOOGLE_WORKSPACE_DIR."
    )


BASE = _find_base()

# Put the bundled google-api libraries on the path if present.
_glibs = os.path.join(BASE, "glibs")
if os.path.isdir(_glibs):
    sys.path.insert(0, _glibs)

from google.oauth2.credentials import Credentials          # noqa: E402
from google.auth.transport.requests import Request          # noqa: E402
from googleapiclient.discovery import build                 # noqa: E402
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload  # noqa: E402


# ── Auth ────────────────────────────────────────────────────────────────────

def get_creds():
    """Build credentials, auto-refreshing (and persisting) an expired token."""
    with open(os.path.join(BASE, "google_token.json")) as f:
        td = json.load(f)
    with open(os.path.join(BASE, "google_client_secret.json")) as f:
        cl = json.load(f)["installed"]
    creds = Credentials(
        token=td["access_token"],
        refresh_token=td.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cl["client_id"],
        client_secret=cl["client_secret"],
        scopes=td["scope"].split(),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        td["access_token"] = creds.token
        with open(os.path.join(BASE, "google_token.json"), "w") as f:
            json.dump(td, f, indent=2)
    return creds


def docs_service():
    return build("docs", "v1", credentials=get_creds())


def sheets_service():
    return build("sheets", "v4", credentials=get_creds())


def drive_service():
    return build("drive", "v3", credentials=get_creds())


def gmail_service():
    return build("gmail", "v1", credentials=get_creds())


# ── Drive: search & list ──────────────────────────────────────────────────────

# Common shortcut MIME types.
MIME = {
    "folder": "application/vnd.google-apps.folder",
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slides": "application/vnd.google-apps.presentation",
    "pdf": "application/pdf",
}


def search_files(query: str, page_size: int = 50, fields: str = None) -> list:
    """Search Drive with a raw Drive query string.

    Examples:
        search_files("name contains 'budget'")
        search_files("mimeType = 'application/vnd.google-apps.folder'")
        search_files("'FOLDER_ID' in parents and trashed = false")
    See https://developers.google.com/drive/api/guides/search-files for syntax.
    """
    fields = fields or "files(id,name,mimeType,modifiedTime,owners,parents,webViewLink),nextPageToken"
    out, token = [], None
    while True:
        resp = drive_service().files().list(
            q=query, pageSize=page_size, fields=fields,
            pageToken=token, includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def list_folder(folder_id: str) -> list:
    """List the non-trashed children of a folder."""
    return search_files(f"'{folder_id}' in parents and trashed = false")


def find_by_name(name: str, exact: bool = False) -> list:
    """Find files/folders by name (substring by default)."""
    op = "=" if exact else "contains"
    safe = name.replace("'", "\\'")
    return search_files(f"name {op} '{safe}' and trashed = false")


def get_metadata(file_id: str) -> dict:
    return drive_service().files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,parents,owners,webViewLink",
        supportsAllDrives=True,
    ).execute()


# ── Drive: upload / download / export ─────────────────────────────────────────

def upload_file(local_path: str, name: str = None, parent_id: str = None,
                mime_type: str = None) -> dict:
    """Upload a local file to Drive. Returns the created file's metadata."""
    meta = {"name": name or os.path.basename(local_path)}
    if parent_id:
        meta["parents"] = [parent_id]
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    return drive_service().files().create(
        body=meta, media_body=media,
        fields="id,name,webViewLink", supportsAllDrives=True,
    ).execute()


def download_file(file_id: str, local_path: str) -> str:
    """Download a binary (non-Google-format) file to local_path."""
    request = drive_service().files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


def export_file(file_id: str, local_path: str, mime_type: str) -> str:
    """Export a Google-format file (Doc/Sheet/Slides) to another format.

    Example mime_types:
        'application/pdf'
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'  (.docx)
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'        (.xlsx)
    """
    request = drive_service().files().export_media(fileId=file_id, mimeType=mime_type)
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


# ── Drive: organize ───────────────────────────────────────────────────────────

def create_folder(name: str, parent_id: str = None) -> dict:
    meta = {"name": name, "mimeType": MIME["folder"]}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive_service().files().create(
        body=meta, fields="id,name,webViewLink", supportsAllDrives=True,
    ).execute()


def move_file(file_id: str, new_parent_id: str) -> dict:
    """Move a file into a new folder (removes all previous parents)."""
    f = drive_service().files().get(
        fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    prev = ",".join(f.get("parents", []))
    return drive_service().files().update(
        fileId=file_id, addParents=new_parent_id, removeParents=prev,
        fields="id,name,parents", supportsAllDrives=True,
    ).execute()


def copy_file(file_id: str, new_name: str = None, parent_id: str = None) -> dict:
    body = {}
    if new_name:
        body["name"] = new_name
    if parent_id:
        body["parents"] = [parent_id]
    return drive_service().files().copy(
        fileId=file_id, body=body,
        fields="id,name,webViewLink", supportsAllDrives=True,
    ).execute()


def rename_file(file_id: str, new_name: str) -> dict:
    return drive_service().files().update(
        fileId=file_id, body={"name": new_name},
        fields="id,name", supportsAllDrives=True,
    ).execute()


def trash_file(file_id: str) -> dict:
    """Move a file to the trash (reversible). Use delete_file for permanent."""
    return drive_service().files().update(
        fileId=file_id, body={"trashed": True},
        fields="id,name,trashed", supportsAllDrives=True,
    ).execute()


def delete_file(file_id: str):
    """Permanently delete a file. Irreversible — prefer trash_file."""
    drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()


# ── Drive: sharing & permissions ──────────────────────────────────────────────

def share_file(file_id: str, email: str, role: str = "reader",
               notify: bool = True, message: str = None) -> dict:
    """Share with a user. role in {reader, commenter, writer, owner}."""
    body = {"type": "user", "role": role, "emailAddress": email}
    return drive_service().permissions().create(
        fileId=file_id, body=body, sendNotificationEmail=notify,
        emailMessage=message, fields="id,role,emailAddress",
        supportsAllDrives=True,
    ).execute()


def share_with_link(file_id: str, role: str = "reader") -> str:
    """Make a file accessible to anyone with the link; return that link."""
    drive_service().permissions().create(
        fileId=file_id, body={"type": "anyone", "role": role},
        fields="id", supportsAllDrives=True,
    ).execute()
    return get_metadata(file_id).get("webViewLink")


def list_permissions(file_id: str) -> list:
    return drive_service().permissions().list(
        fileId=file_id,
        fields="permissions(id,type,role,emailAddress,displayName)",
        supportsAllDrives=True,
    ).execute().get("permissions", [])


def remove_permission(file_id: str, permission_id: str):
    drive_service().permissions().delete(
        fileId=file_id, permissionId=permission_id, supportsAllDrives=True,
    ).execute()


# ── Docs ──────────────────────────────────────────────────────────────────────

def create_doc(title: str, text: str = None, parent_id: str = None) -> dict:
    """Create a native Google Doc and optionally seed it with initial text.

    The Doc is created empty via the Drive API (body mimeType = Google Doc, no
    media — which sidesteps the "Invalid MIME type for the uploaded content"
    error you get when trying to upload bytes *as* a Google type). If `text` is
    given, it's inserted afterwards through the Docs API. Returns the created
    file's metadata (id, name, webViewLink)."""
    meta = {"name": title, "mimeType": MIME["doc"]}
    if parent_id:
        meta["parents"] = [parent_id]
    f = drive_service().files().create(
        body=meta, fields="id,name,webViewLink", supportsAllDrives=True,
    ).execute()
    if text:
        append_to_doc(f["id"], text)
    return f


def read_doc(doc_id: str) -> str:
    """Return the full plain text of a Google Doc."""
    doc = docs_service().documents().get(documentId=doc_id).execute()
    text = []
    for el in doc.get("body", {}).get("content", []):
        if "paragraph" in el:
            for pe in el["paragraph"].get("elements", []):
                text.append(pe.get("textRun", {}).get("content", ""))
    return "".join(text)


def append_to_doc(doc_id: str, text: str):
    doc = docs_service().documents().get(documentId=doc_id).execute()
    end = doc["body"]["content"][-1]["endIndex"] - 1
    docs_service().documents().batchUpdate(documentId=doc_id, body={
        "requests": [{"insertText": {"location": {"index": end}, "text": text}}]
    }).execute()


def replace_in_doc(doc_id: str, old: str, new: str):
    docs_service().documents().batchUpdate(documentId=doc_id, body={
        "requests": [{"replaceAllText": {
            "containsText": {"text": old, "matchCase": True},
            "replaceText": new,
        }}]
    }).execute()


def add_doc_comment(doc_id: str, text: str):
    """Add a comment as the authenticated user (Drive API)."""
    drive_service().comments().create(
        fileId=doc_id, body={"content": text}, fields="id,content,author",
    ).execute()


def list_doc_comments(doc_id: str) -> list:
    return drive_service().comments().list(
        fileId=doc_id, fields="comments(id,content,author,resolved,replies)",
    ).execute().get("comments", [])


def list_versions(file_id: str) -> list:
    return drive_service().revisions().list(
        fileId=file_id,
        fields="revisions(id,modifiedTime,lastModifyingUser,exportLinks)",
    ).execute().get("revisions", [])


# ── Sheets ──────────────────────────────────────────────────────────────────

def read_sheet(spreadsheet_id: str, range_: str) -> list:
    return sheets_service().spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_,
    ).execute().get("values", [])


def write_sheet(spreadsheet_id: str, range_: str, values: list):
    sheets_service().spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_,
        valueInputOption="USER_ENTERED", body={"values": values},
    ).execute()


def append_sheet(spreadsheet_id: str, range_: str, values: list):
    sheets_service().spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=range_,
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()


# ── Gmail ─────────────────────────────────────────────────────────────────
# Requires the gmail.modify scope (trash + drafts). Permanent delete_message
# additionally needs the full https://mail.google.com/ scope.

def gmail_search(query: str, max_results: int = 25) -> list:
    """Search messages with Gmail query syntax, e.g.
    gmail_search("from:boss@x.com is:unread newer_than:7d").
    Returns lightweight stubs [{'id', 'threadId'}]; use get_message for detail.
    """
    svc = gmail_service()
    out, token = [], None
    while True:
        resp = svc.users().messages().list(
            userId="me", q=query, maxResults=min(max_results, 500),
            pageToken=token,
        ).execute()
        out.extend(resp.get("messages", []))
        token = resp.get("nextPageToken")
        if not token or len(out) >= max_results:
            break
    return out[:max_results]


def get_message(msg_id: str, fmt: str = "metadata") -> dict:
    """Fetch a message. fmt in {metadata, full, minimal, raw}."""
    kwargs = {"userId": "me", "id": msg_id, "format": fmt}
    if fmt == "metadata":
        kwargs["metadataHeaders"] = ["From", "To", "Subject", "Date"]
    return gmail_service().users().messages().get(**kwargs).execute()


def trash_message(msg_id: str) -> dict:
    """Move a message to Trash (reversible)."""
    return gmail_service().users().messages().trash(
        userId="me", id=msg_id).execute()


def untrash_message(msg_id: str) -> dict:
    """Restore a message from Trash."""
    return gmail_service().users().messages().untrash(
        userId="me", id=msg_id).execute()


def delete_message(msg_id: str):
    """Permanently delete a message (irreversible, bypasses Trash).
    Needs the full https://mail.google.com/ scope — gmail.modify is not enough.
    Prefer trash_message."""
    gmail_service().users().messages().delete(userId="me", id=msg_id).execute()


def _build_raw(to, subject, body, cc=None, bcc=None, sender=None) -> dict:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if sender:
        msg["From"] = sender
    msg.set_content(body or "")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def list_drafts(max_results: int = 50) -> list:
    return gmail_service().users().drafts().list(
        userId="me", maxResults=max_results).execute().get("drafts", [])


def get_draft(draft_id: str, fmt: str = "full") -> dict:
    return gmail_service().users().drafts().get(
        userId="me", id=draft_id, format=fmt).execute()


def create_draft(to: str, subject: str, body: str,
                 cc: str = None, bcc: str = None) -> dict:
    """Create a draft. Returns the draft (with id)."""
    message = _build_raw(to, subject, body, cc, bcc)
    return gmail_service().users().drafts().create(
        userId="me", body={"message": message}).execute()


def update_draft(draft_id: str, to: str, subject: str, body: str,
                 cc: str = None, bcc: str = None) -> dict:
    """Replace a draft's content. The draft id is preserved; the old MIME
    message is discarded and replaced by the new one (Gmail API semantics)."""
    message = _build_raw(to, subject, body, cc, bcc)
    return gmail_service().users().drafts().update(
        userId="me", id=draft_id, body={"message": message}).execute()


def send_draft(draft_id: str) -> dict:
    """Send an existing draft."""
    return gmail_service().users().drafts().send(
        userId="me", body={"id": draft_id}).execute()


def delete_draft(draft_id: str):
    """Permanently delete a draft (not just trash it)."""
    gmail_service().users().drafts().delete(userId="me", id=draft_id).execute()


if __name__ == "__main__":
    get_creds()
    print(f"✅ Credentials OK (base: {BASE})")
