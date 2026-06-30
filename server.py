#!/usr/bin/env python3
"""
Google Workspace MCP server.

Exposes the user's Google Drive / Docs / Sheets / Gmail (via the OAuth
credentials they already authorized) as MCP tools, so Claude can use them in
*any* session without mounting a credentials folder. The server runs host-side
(launched by Claude Desktop from claude_desktop_config), so it reads the
credential files directly off the host filesystem.

Credential resolution (in priority order):

  1. GOOGLE_WORKSPACE_DIR — absolute path to a folder containing
     google_token.json + google_client_secret.json. Simplest: point this at
     your existing ~/Dokumentumok/Claude/google-workspace folder.

  2. GOOGLE_TOKEN_JSON + GOOGLE_CLIENT_SECRET_JSON — the *contents* of those
     two files, inline. Useful if you'd rather keep everything in
     claude_desktop_config and not depend on a folder at all. They're written
     to a private temp dir at startup and used from there.

  3. Fallback: the helper's own auto-discovery (globs the home directory for a
     google-workspace folder).

The bundled google_helper.py holds all the actual API logic — this file only
loads credentials and re-exports the helper functions as MCP tools.
"""
import json
import os
import sys
import tempfile

# ── Resolve credentials BEFORE importing the helper ──────────────────────────
# google_helper resolves its credentials at import time, so we set up the
# environment first.


def _materialize_inline_credentials() -> str | None:
    """If credentials were passed inline via env, write them to a temp dir and
    return that dir. Returns None if inline creds weren't provided."""
    token = os.environ.get("GOOGLE_TOKEN_JSON")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")
    if not (token and secret):
        return None
    # Validate they're real JSON so we fail loudly with a clear message.
    try:
        json.loads(token)
        json.loads(secret)
    except json.JSONDecodeError as e:
        sys.exit(f"GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_SECRET_JSON is not valid JSON: {e}")
    base = tempfile.mkdtemp(prefix="gws_creds_")
    os.chmod(base, 0o700)
    with open(os.path.join(base, "google_token.json"), "w") as f:
        f.write(token)
    with open(os.path.join(base, "google_client_secret.json"), "w") as f:
        f.write(secret)
    return base


if "GOOGLE_WORKSPACE_DIR" not in os.environ:
    inline = _materialize_inline_credentials()
    if inline:
        os.environ["GOOGLE_WORKSPACE_DIR"] = inline

# Make sure the bundled helper is importable regardless of the launch cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import google_helper as g  # noqa: E402
except FileNotFoundError as e:
    sys.exit(
        "Could not locate Google credentials. Set GOOGLE_WORKSPACE_DIR to the "
        "folder holding google_token.json + google_client_secret.json, or pass "
        f"them inline via GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_SECRET_JSON.\n\n{e}"
    )

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("google-workspace")


# ── Drive: search & list ─────────────────────────────────────────────────────

@mcp.tool()
def search_files(query: str, page_size: int = 50) -> list:
    """Search Drive with a raw Drive query string, e.g.
    "name contains 'budget'" or "'FOLDER_ID' in parents and trashed = false".
    See https://developers.google.com/drive/api/guides/search-files."""
    return g.search_files(query, page_size=page_size)


@mcp.tool()
def find_by_name(name: str, exact: bool = False) -> list:
    """Find files/folders by name (substring match unless exact=True)."""
    return g.find_by_name(name, exact=exact)


@mcp.tool()
def list_folder(folder_id: str) -> list:
    """List the non-trashed children of a folder."""
    return g.list_folder(folder_id)


@mcp.tool()
def get_metadata(file_id: str) -> dict:
    """Get a file's metadata (id, name, mimeType, size, parents, link, ...)."""
    return g.get_metadata(file_id)


# ── Drive: upload / download / export ────────────────────────────────────────

@mcp.tool()
def upload_file(local_path: str, name: str = "", parent_id: str = "",
                mime_type: str = "") -> dict:
    """Upload a local file to Drive. Returns the created file's metadata."""
    return g.upload_file(local_path, name=name or None,
                         parent_id=parent_id or None, mime_type=mime_type or None)


@mcp.tool()
def download_file(file_id: str, local_path: str) -> str:
    """Download a binary (non-Google-format) file to local_path."""
    return g.download_file(file_id, local_path)


@mcp.tool()
def export_file(file_id: str, local_path: str, mime_type: str) -> str:
    """Export a Google-format file (Doc/Sheet/Slides) to another format, e.g.
    'application/pdf' or the .docx / .xlsx OOXML mime types."""
    return g.export_file(file_id, local_path, mime_type)


# ── Drive: organize ──────────────────────────────────────────────────────────

@mcp.tool()
def create_folder(name: str, parent_id: str = "") -> dict:
    """Create a folder, optionally inside parent_id."""
    return g.create_folder(name, parent_id=parent_id or None)


@mcp.tool()
def move_file(file_id: str, new_parent_id: str) -> dict:
    """Move a file into a new folder (removes previous parents)."""
    return g.move_file(file_id, new_parent_id)


@mcp.tool()
def copy_file(file_id: str, new_name: str = "", parent_id: str = "") -> dict:
    """Copy a file, optionally renaming and placing it in parent_id."""
    return g.copy_file(file_id, new_name=new_name or None, parent_id=parent_id or None)


@mcp.tool()
def rename_file(file_id: str, new_name: str) -> dict:
    """Rename a file or folder."""
    return g.rename_file(file_id, new_name)


@mcp.tool()
def trash_file(file_id: str) -> dict:
    """Move a file to Trash (reversible). Prefer this over delete_file."""
    return g.trash_file(file_id)


@mcp.tool()
def delete_file(file_id: str) -> dict:
    """Permanently delete a file. Irreversible — prefer trash_file."""
    g.delete_file(file_id)
    return {"deleted": file_id}


# ── Drive: sharing & permissions ─────────────────────────────────────────────

@mcp.tool()
def share_file(file_id: str, email: str, role: str = "reader",
               notify: bool = True, message: str = "") -> dict:
    """Share a file with a user. role in {reader, commenter, writer, owner}."""
    return g.share_file(file_id, email, role=role, notify=notify,
                        message=message or None)


@mcp.tool()
def share_with_link(file_id: str, role: str = "reader") -> str:
    """Make a file accessible to anyone with the link; returns that link.
    Confirm with the user before using — this exposes the file publicly."""
    return g.share_with_link(file_id, role=role)


@mcp.tool()
def list_permissions(file_id: str) -> list:
    """List who has access to a file."""
    return g.list_permissions(file_id)


@mcp.tool()
def remove_permission(file_id: str, permission_id: str) -> dict:
    """Revoke a specific permission from a file."""
    g.remove_permission(file_id, permission_id)
    return {"removed": permission_id}


# ── Docs ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_doc(title: str, text: str = "", parent_id: str = "") -> dict:
    """Create a native Google Doc, optionally seeded with initial text and
    placed inside parent_id. Returns the created file's metadata."""
    return g.create_doc(title, text=text or None, parent_id=parent_id or None)


@mcp.tool()
def read_doc(doc_id: str) -> str:
    """Return the full plain text of a Google Doc."""
    return g.read_doc(doc_id)


@mcp.tool()
def append_to_doc(doc_id: str, text: str) -> dict:
    """Append text to the end of a Google Doc."""
    g.append_to_doc(doc_id, text)
    return {"ok": True, "doc_id": doc_id}


@mcp.tool()
def replace_in_doc(doc_id: str, old: str, new: str) -> dict:
    """Replace all occurrences of `old` with `new` in a Google Doc (case-sensitive)."""
    g.replace_in_doc(doc_id, old, new)
    return {"ok": True, "doc_id": doc_id}


@mcp.tool()
def add_doc_comment(doc_id: str, text: str) -> dict:
    """Add a comment to a Doc as the signed-in user."""
    g.add_doc_comment(doc_id, text)
    return {"ok": True, "doc_id": doc_id}


@mcp.tool()
def list_doc_comments(doc_id: str) -> list:
    """List comments on a Doc."""
    return g.list_doc_comments(doc_id)


@mcp.tool()
def list_versions(file_id: str) -> list:
    """List the revision history of a Doc or Sheet."""
    return g.list_versions(file_id)


# ── Sheets ───────────────────────────────────────────────────────────────────

@mcp.tool()
def read_sheet(spreadsheet_id: str, range_: str) -> list:
    """Read a range, e.g. read_sheet(ID, "Sheet1!A1:Z100"). Returns rows."""
    return g.read_sheet(spreadsheet_id, range_)


@mcp.tool()
def write_sheet(spreadsheet_id: str, range_: str, values: list) -> dict:
    """Write `values` (list of rows) into a range, overwriting existing cells."""
    g.write_sheet(spreadsheet_id, range_, values)
    return {"ok": True, "spreadsheet_id": spreadsheet_id, "range": range_}


@mcp.tool()
def append_sheet(spreadsheet_id: str, range_: str, values: list) -> dict:
    """Append `values` (list of rows) as new rows after a range."""
    g.append_sheet(spreadsheet_id, range_, values)
    return {"ok": True, "spreadsheet_id": spreadsheet_id, "range": range_}


# ── Gmail ────────────────────────────────────────────────────────────────────

@mcp.tool()
def gmail_search(query: str, max_results: int = 25) -> list:
    """Search messages with Gmail query syntax, e.g.
    "from:boss@x.com is:unread newer_than:7d". Returns [{id, threadId}]."""
    return g.gmail_search(query, max_results=max_results)


@mcp.tool()
def get_message(msg_id: str, fmt: str = "metadata") -> dict:
    """Fetch a message. fmt in {metadata, full, minimal, raw}."""
    return g.get_message(msg_id, fmt=fmt)


@mcp.tool()
def trash_message(msg_id: str) -> dict:
    """Move a message to Trash (reversible)."""
    return g.trash_message(msg_id)


@mcp.tool()
def untrash_message(msg_id: str) -> dict:
    """Restore a message from Trash."""
    return g.untrash_message(msg_id)


@mcp.tool()
def delete_message(msg_id: str) -> dict:
    """Permanently delete a message (needs full mail.google.com scope).
    Prefer trash_message."""
    g.delete_message(msg_id)
    return {"deleted": msg_id}


@mcp.tool()
def list_drafts(max_results: int = 50) -> list:
    """List draft messages."""
    return g.list_drafts(max_results=max_results)


@mcp.tool()
def get_draft(draft_id: str, fmt: str = "full") -> dict:
    """Fetch a draft's content."""
    return g.get_draft(draft_id, fmt=fmt)


@mcp.tool()
def create_draft(to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> dict:
    """Create a draft email. Returns the draft (with id)."""
    return g.create_draft(to, subject, body, cc=cc or None, bcc=bcc or None)


@mcp.tool()
def update_draft(draft_id: str, to: str, subject: str, body: str,
                 cc: str = "", bcc: str = "") -> dict:
    """Replace a draft's content (id kept, content replaced)."""
    return g.update_draft(draft_id, to, subject, body, cc=cc or None, bcc=bcc or None)


@mcp.tool()
def send_draft(draft_id: str) -> dict:
    """Send an existing draft."""
    return g.send_draft(draft_id)


@mcp.tool()
def delete_draft(draft_id: str) -> dict:
    """Permanently delete a draft."""
    g.delete_draft(draft_id)
    return {"deleted": draft_id}


if __name__ == "__main__":
    mcp.run()
