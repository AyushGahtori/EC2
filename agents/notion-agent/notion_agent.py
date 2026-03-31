import os
from typing import Any

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")


class NotionAgentError(Exception):
    pass


class NotionAgent:
    def __init__(self, access_token: str):
        self.access_token = access_token.strip()
        if not self.access_token:
            raise NotionAgentError("Notion access token is missing.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    def search_pages(self, query: str, limit: int = 10) -> dict[str, Any]:
        response = requests.post(
            f"{NOTION_API}/search",
            headers=self.headers,
            json={
                "query": query,
                "filter": {"value": "page", "property": "object"},
                "page_size": max(1, min(limit, 20)),
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            },
            timeout=30,
        )
        self._raise_for_status(response)

        pages = [
            {
                "id": page["id"],
                "title": self._extract_page_title(page),
                "url": page.get("url", ""),
                "lastEdited": page.get("last_edited_time", ""),
            }
            for page in response.json().get("results", [])
        ]
        return {"pages": pages}

    def get_page(self, page_id: str) -> dict[str, Any]:
        page_response = requests.get(
            f"{NOTION_API}/pages/{page_id}",
            headers=self.headers,
            timeout=30,
        )
        self._raise_for_status(page_response)
        page = page_response.json()

        blocks_response = requests.get(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=self.headers,
            params={"page_size": 100},
            timeout=30,
        )
        self._raise_for_status(blocks_response)
        blocks = blocks_response.json().get("results", [])

        content = "\n".join(filter(None, (self._extract_block_text(block) for block in blocks)))
        return {
            "page": {
                "id": page["id"],
                "title": self._extract_page_title(page),
                "url": page.get("url", ""),
                "lastEdited": page.get("last_edited_time", ""),
                "content": content[:4000],
            }
        }

    def create_page(
        self,
        title: str,
        content: str,
        parent_page_id: str | None = None,
    ) -> dict[str, Any]:
        parent = {"page_id": parent_page_id} if parent_page_id else self._discover_parent()
        response = requests.post(
            f"{NOTION_API}/pages",
            headers=self.headers,
            json={
                "parent": parent,
                "properties": {
                    "title": {
                        "title": [{"text": {"content": title}}],
                    }
                },
                "children": self._text_to_blocks(content),
            },
            timeout=30,
        )
        self._raise_for_status(response)
        created = response.json()
        return {
            "page": {
                "id": created["id"],
                "title": title,
                "url": created.get("url", ""),
            }
        }

    def append_to_page(self, page_id: str, content: str) -> dict[str, Any]:
        response = requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=self.headers,
            json={"children": self._text_to_blocks(content)},
            timeout=30,
        )
        self._raise_for_status(response)
        return {"pageId": page_id}

    def _discover_parent(self) -> dict[str, str]:
        response = requests.post(
            f"{NOTION_API}/search",
            headers=self.headers,
            json={
                "filter": {"value": "page", "property": "object"},
                "page_size": 1,
            },
            timeout=30,
        )
        self._raise_for_status(response)
        results = response.json().get("results", [])
        if not results:
            raise NotionAgentError(
                "No accessible Notion pages found. Share at least one page with the integration first."
            )
        return {"page_id": results[0]["id"]}

    def _text_to_blocks(self, text: str) -> list[dict[str, Any]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            lines = [" "]

        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line[:1900]}}],
                },
            }
            for line in lines[:80]
        ]

    def _extract_page_title(self, page: dict[str, Any]) -> str:
        properties = page.get("properties", {})
        for key in ("title", "Name", "name"):
            candidate = properties.get(key)
            if isinstance(candidate, dict):
                title_items = candidate.get("title", [])
                if title_items:
                    return title_items[0].get("plain_text", "(untitled)")
        return "(untitled)"

    def _extract_block_text(self, block: dict[str, Any]) -> str:
        block_type = block.get("type", "")
        block_payload = block.get(block_type, {})
        if not isinstance(block_payload, dict):
            return ""

        rich_text = block_payload.get("rich_text", [])
        return "".join(item.get("plain_text", "") for item in rich_text if isinstance(item, dict))

    def _raise_for_status(self, response: requests.Response) -> None:
        if response.ok:
            return

        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        raise NotionAgentError(f"Notion API error ({response.status_code}): {payload}")
