import os
import requests
import msal
from typing import Any, Dict

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_SCOPES = [
    "User.Read", 
    "People.Read", 
    "User.ReadBasic.All",
    "Calendars.ReadWrite",
    "Mail.ReadWrite",
    "Mail.Send"
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
    """Provides a shared Microsoft Graph API client for all agents."""

    def __init__(self) -> None:
        if not GRAPH_CLIENT_ID:
            raise RuntimeError(
                "GRAPH_CLIENT_ID is required. "
                "Set it in the agent server's .env file."
            )

        if not auth_store.get("msal_app"):
            authority = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}"
            auth_store["msal_app"] = msal.PublicClientApplication(
                GRAPH_CLIENT_ID,
                authority=authority,
            )
        self.app = auth_store["msal_app"]

    def acquire_token(self) -> str:
        """Acquire a Graph access token. Raises DeviceFlowRequired if a device flow is started."""
        token = auth_store.get("token")
        if token and isinstance(token, str):
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
                return str(access_token)

        # If a flow is already pending, check if it's expired
        flow = auth_store.get("flow")
        if flow:
            import time
            if time.time() < flow.get("expires_at", 0):
                raise DeviceFlowRequired(flow)
            else:
                # Flow expired, clear it
                auth_store["flow"] = None

        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError("Could not start Microsoft sign-in device flow.")

        import time
        flow["expires_at"] = time.time() + flow.get("expires_in", 900)
        auth_store["flow"] = flow
        raise DeviceFlowRequired(flow)

    def get(self, path: str, params: dict | None = None) -> dict:
        """Perform a GET request to the Graph API."""
        url = f"{GRAPH_BASE_URL}{path}"
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.acquire_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code == 204:
            return {}
        return response.json()

    def post(self, path: str, json_data: dict | None = None) -> dict:
        """Perform a POST request to the Graph API."""
        url = f"{GRAPH_BASE_URL}{path}"
        response = requests.post(
            url,
            json=json_data,
            headers={"Authorization": f"Bearer {self.acquire_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code in (202, 204):
            return {}
        return response.json()
        
    def patch(self, path: str, json_data: dict | None = None) -> dict:
        """Perform a PATCH request to the Graph API."""
        url = f"{GRAPH_BASE_URL}{path}"
        response = requests.patch(
            url,
            json=json_data,
            headers={"Authorization": f"Bearer {self.acquire_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code in (202, 204):
            return {}
        return response.json()
        
    def delete(self, path: str, params: dict | None = None) -> dict:
        """Perform a DELETE request to the Graph API."""
        url = f"{GRAPH_BASE_URL}{path}"
        response = requests.delete(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.acquire_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code in (202, 204):
            return {}
        try:
            return response.json()
        except Exception:
            return {}
