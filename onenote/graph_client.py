import os

import msal
import requests
from msal import SerializableTokenCache

from config import MICROSOFT_CLIENT_ID

TOKEN_CACHE_FILE = "token_cache.bin"


class GraphClient:
    def __init__(self):
        cache = SerializableTokenCache()

        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                cache.deserialize(f.read())

        self.app = msal.PublicClientApplication(
            MICROSOFT_CLIENT_ID,
            authority="https://login.microsoftonline.com/common",
            token_cache=cache
        )
        self.cache = cache
        self.access_token = None

    def authenticate(self):
        scopes = ["User.Read", "Notes.ReadWrite"]
        used_interactive = False

        accounts = self.app.get_accounts()

        result = None

        if accounts:
            result = self.app.acquire_token_silent(
                scopes,
                account=accounts[0]
            )

        if not result:
            used_interactive = True
            result = self.app.acquire_token_interactive(scopes=scopes)

        if "access_token" in result:
            self.access_token = result["access_token"]
            if used_interactive and self.cache.has_state_changed:
                with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(self.cache.serialize())
            print("✅ Microsoft authentication successful.")
            return

        raise Exception(result.get("error_description") or result.get("error"))

    def get_profile(self):
        if self.access_token is None:
            raise Exception("Microsoft Graph client is not authenticated.")

        response = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={
                "Authorization": f"Bearer {self.access_token}"
            }
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(response.text)
