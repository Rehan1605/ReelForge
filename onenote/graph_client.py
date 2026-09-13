import os
import re
from datetime import datetime, timezone
from onenote.formatter import escape_html as escape


import msal
import requests
from msal import SerializableTokenCache

from config import MICROSOFT_CLIENT_ID

AUTHORITY = "https://login.microsoftonline.com/common"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["User.Read", "Notes.ReadWrite"]
TOKEN_CACHE_FILE = "token_cache.bin"


def _load_user_microsoft_cache(user_id):
    """Load the serialized MSAL token cache for a per-user Microsoft connection."""
    try:
        from storage.user import get_user_microsoft
        ms_data = get_user_microsoft(user_id)
        if ms_data and ms_data.get("token_cache"):
            return ms_data["token_cache"]
    except Exception:
        pass
    return None


def _save_user_microsoft_cache(user_id, token_cache, refreshed_at):
    """Write the serialized MSAL token cache back to the per-user MongoDB document."""
    try:
        from storage.user import update_user_microsoft
        update_user_microsoft(user_id, {
            "token_cache": token_cache,
            "token_refreshed_at": refreshed_at,
        })
    except Exception:
        pass


class GraphClient:
    def __init__(self, user_id=None):
        """
        Create a GraphClient for a ReelForge user.

        Args:
            user_id: ReelForge user_id ('usr_...'). When provided, the MSAL
                token cache is loaded from the user's per-user Microsoft
                connection in MongoDB and saved back there after
                authentication. When None, the legacy global token_cache.bin is
                used (preserving pre-V3.3 behavior).
        """
        self.user_id = user_id
        cache = SerializableTokenCache()

        if user_id:
            stored_cache = _load_user_microsoft_cache(user_id)
            self._had_stored_cache = bool(stored_cache)
            if stored_cache:
                cache.deserialize(stored_cache)
        else:
            self._had_stored_cache = False
            if os.path.exists(TOKEN_CACHE_FILE):
                with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache.deserialize(f.read())

        self.app = msal.PublicClientApplication(
            MICROSOFT_CLIENT_ID,
            authority=AUTHORITY,
            token_cache=cache
        )
        self.cache = cache
        self.access_token = None

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}"
        }

    def _extract_body_content(self, html_content):
        match = re.search(
            r"<body[^>]*>(.*?)</body>",
            html_content,
            flags=re.IGNORECASE | re.DOTALL
        )

        if match:
            return match.group(1).strip()

        return html_content.strip()

    def authenticate(self):
        used_interactive = False

        accounts = self.app.get_accounts()

        result = None

        if accounts:
            result = self.app.acquire_token_silent(
                GRAPH_SCOPES,
                account=accounts[0]
            )

        if not result:
            if self.user_id:
                if not self._had_stored_cache:
                    raise Exception(
                        "Microsoft account not connected. Please use /connect in Telegram."
                    )
                raise Exception(
                    "Per-user Microsoft authentication failed for this account. "
                    "Please reconnect via /connect in Telegram."
                )
            used_interactive = True
            result = self.app.acquire_token_interactive(scopes=GRAPH_SCOPES)

        if "access_token" in result:
            self.access_token = result["access_token"]
            if self.user_id:
                if self.cache.has_state_changed:
                    _save_user_microsoft_cache(
                        self.user_id,
                        self.cache.serialize(),
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    )
            elif used_interactive and self.cache.has_state_changed:
                with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(self.cache.serialize())
            print("[OK] Microsoft authentication successful.")
            return

        if self.user_id:
            if not self._had_stored_cache:
                raise Exception(
                    "Microsoft account not connected. Please use /connect in Telegram."
                )
            raise Exception(
                "Per-user Microsoft authentication failed for this account. "
                "Please reconnect via /connect in Telegram."
            )

        raise Exception(result.get("error_description") or result.get("error"))

    def get_profile(self):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        response = requests.get(
            f"{GRAPH_BASE_URL}/me",
            headers=self._headers()
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(response.text)

    def list_notebooks(self):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        response = requests.get(
            f"{GRAPH_BASE_URL}/me/onenote/notebooks",
            headers=self._headers()
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(response.text)

    def get_or_create_notebook(self, name):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        notebooks = self.list_notebooks()

        for notebook in notebooks.get("value", []):
            if notebook.get("displayName") == name:
                return notebook

        response = requests.post(
            f"{GRAPH_BASE_URL}/me/onenote/notebooks",
            headers={
                **self._headers(),
                "Content-Type": "application/json"
            },
            json={
                "displayName": name
            }
        )

        if response.status_code == 201:
            return response.json()

        raise Exception(response.text)

    def list_sections(self, notebook_id):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        response = requests.get(
            f"{GRAPH_BASE_URL}/me/onenote/notebooks/{notebook_id}/sections",
            headers=self._headers()
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(response.text)

    def create_section(self, notebook_id, section_name):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        sections_url = (
            f"{GRAPH_BASE_URL}/me/onenote/notebooks/{notebook_id}/sections"
        )
        headers = {
            **self._headers(),
            "Content-Type": "application/json"
        }

        sections = self.list_sections(notebook_id)

        for section in sections.get("value", []):
            if section.get("displayName") == section_name:
                return section

        response = requests.post(
            sections_url,
            headers=headers,
            json={
                "displayName": section_name
            }
        )

        if response.status_code == 201:
            return response.json()

        raise Exception(response.text)

    def create_page(self, section_id, title, html_content, thumbnail_path=None):
        if self.access_token is None:
            raise Exception("Not authenticated.")

        body_content = self._extract_body_content(html_content)
        page_html = f"""<!DOCTYPE html>
<html lang="en-US">
<head>
    <title>{escape(str(title))}</title>
    <meta charset="utf-8" />
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
</head>
<body data-absolute-enabled="true" style="font-family:Calibri, Segoe UI, sans-serif; font-size:11pt">
{body_content}
</body>
</html>
"""


        endpoint = f"{GRAPH_BASE_URL}/me/onenote/sections/{section_id}/pages"

        # Attempt safe multipart image embedding if local thumbnail exists
        if thumbnail_path and os.path.isfile(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            try:
                from pathlib import Path
                ext = Path(thumbnail_path).suffix.lower()
                mime_type = "image/png" if ext == ".png" else "image/jpeg"

                with open(thumbnail_path, "rb") as img_f:
                    img_bytes = img_f.read()

                files = {
                    "Presentation": (None, page_html.encode("utf-8"), "application/xhtml+xml; charset=utf-8"),
                    "thumbnail": ("thumbnail.jpg", img_bytes, mime_type),
                }

                response = requests.post(
                    endpoint,
                    headers=self._headers(),
                    files=files,
                    timeout=30,
                )

                if response.status_code == 201:
                    return response.json()
                else:
                    print(f"Notice: Multipart OneNote page creation returned HTTP {response.status_code}. Falling back to standard XHTML.")
            except Exception as e:
                print(f"Notice: Multipart OneNote page upload error ({e}). Falling back to standard XHTML.")

        # Single-part standard XHTML page creation (fallback or default when no thumbnail)
        cleaned_html = re.sub(r'<div[^>]*>\s*<img\s+src="name:thumbnail"[^>]*>\s*</div>', '', page_html)
        cleaned_html = re.sub(r'<img\s+src="name:thumbnail"[^>]*>', '', cleaned_html)

        response = requests.post(
            endpoint,
            headers={
                **self._headers(),
                "Content-Type": "application/xhtml+xml; charset=utf-8"
            },
            data=cleaned_html.encode("utf-8"),
            timeout=30,
        )

        if response.status_code == 201:
            return response.json()

        raise Exception(response.text)
