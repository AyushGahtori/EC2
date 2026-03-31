import os
from typing import Any, Dict

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "common")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "") or os.getenv("MICROSOFT_CLIENT_ID", "")
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "") or GRAPH_CLIENT_ID
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")

GRAPH_SCOPES = [
    "User.Read",
    "People.Read",
    "User.ReadBasic.All",
    "Calendars.ReadWrite",
    "Mail.ReadWrite",
    "Mail.Send",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "offline_access",
]

auth_store: Dict[str, Any] = {
    "flow": None,
    "token": None,
    "msal_app": None,
}


class DeviceFlowRequired(Exception):
    def __init__(self, flow_data):
        self.flow_data = flow_data
        super().__init__("Microsoft sign-in required")


class GraphClient:
    """Microsoft Graph client with token passthrough for production and device flow fallback for local debug."""

    def __init__(self, access_token: str | None = None, refresh_token: str | None = None) -> None:
        if not GRAPH_CLIENT_ID:
            raise RuntimeError(
                "GRAPH_CLIENT_ID or MICROSOFT_CLIENT_ID is required. "
                "Set it in the agent server's environment."
            )

        self.access_token = access_token or None
        self.refresh_token = refresh_token or None

        if not auth_store.get("msal_app"):
            authority = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}"
            auth_store["msal_app"] = msal.PublicClientApplication(
                GRAPH_CLIENT_ID,
                authority=authority,
            )
        self.app = auth_store["msal_app"]

    def _refresh_delegated_token(self) -> str:
        if not self.refresh_token:
            raise DeviceFlowRequired(auth_store.get("flow"))
        if not MICROSOFT_CLIENT_ID or not MICROSOFT_CLIENT_SECRET:
            raise RuntimeError(
                "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET are required to refresh Microsoft tokens."
            )

        response = requests.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": MICROSOFT_CLIENT_ID,
                "client_secret": MICROSOFT_CLIENT_SECRET,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(GRAPH_SCOPES),
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        self.access_token = data["access_token"]
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        return self.access_token

    def acquire_token(self) -> str:
        if self.access_token:
            return self.access_token

        token = auth_store.get("token")
        if token and isinstance(token, str):
            self.access_token = token
            return token

        app = self.app
        if not app:
            raise RuntimeError("MSAL app not initialized")

        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
            if result and "access_token" in result:
                access_token = result["access_token"]
                auth_store["token"] = access_token
                self.access_token = access_token
                return access_token

        flow = auth_store.get("flow")
        if flow:
            import time

            if time.time() < flow.get("expires_at", 0):
                raise DeviceFlowRequired(flow)
            auth_store["flow"] = None

        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError("Could not start Microsoft sign-in device flow.")

        import time

        flow["expires_at"] = time.time() + flow.get("expires_in", 900)
        auth_store["flow"] = flow
        raise DeviceFlowRequired(flow)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{GRAPH_BASE_URL}{path}"
        headers = kwargs.pop("headers", {})
        token = self.acquire_token()

        response = requests.request(
            method,
            url,
            headers={**headers, "Authorization": f"Bearer {token}"},
            timeout=30,
            **kwargs,
        )

        if response.status_code == 401 and self.refresh_token:
            refreshed_token = self._refresh_delegated_token()
            response = requests.request(
                method,
                url,
                headers={**headers, "Authorization": f"Bearer {refreshed_token}"},
                timeout=30,
                **kwargs,
            )

        response.raise_for_status()
        if response.status_code in (202, 204):
            return {}

        try:
            return response.json()
        except Exception:
            return {}

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_data: dict | None = None) -> dict:
        return self._request("POST", path, json=json_data)

    def patch(self, path: str, json_data: dict | None = None) -> dict:
        return self._request("PATCH", path, json=json_data)

    def delete(self, path: str, params: dict | None = None) -> dict:
        return self._request("DELETE", path, params=params)
