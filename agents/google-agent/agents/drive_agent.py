"""
Google Drive Agent
File operations
"""

import json
import logging
import mimetypes
from pathlib import Path
from typing import Dict, Any, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class DriveAgent(BaseAgent):
    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = await self._determine_action(user_message, context)

        if action == "list":
            return await self.list_files()
        if action == "search":
            return await self.search_files(user_message, context)
        if action == "upload":
            return await self.upload_file(user_message, context)
        if action == "read":
            return await self.read_file(user_message, context)
        return await self.list_files()

    async def _determine_action(
        self,
        msg: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        params = await self.extract_parameters(
            user_message=msg,
            schema_description='action: one of "list", "search", "read", "upload"',
            example_output='{"action": "list"}',
            context=context,
        )
        return params.get("action", "list")

    async def list_files(self) -> Dict[str, Any]:
        try:
            response = await self.request_google_api(
                "GET",
                f"{DRIVE_BASE_URL}/files",
                params={
                    "pageSize": 10,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                    "orderBy": "modifiedTime desc",
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc)

        if response.status_code == 200:
            files = response.json().get("files", [])
            if not files:
                return self.success(summary="Your Google Drive is empty.", data={"files": []})

            file_list = "\n".join(
                f"- {file_data['name']} ({file_data.get('mimeType', 'unknown')})"
                for file_data in files
            )
            return self.success(
                summary=f"Your Drive files:\n{file_list}",
                data={"files": files},
            )

        return self.handle_google_api_error("Drive", response)

    async def search_files(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='query: search term for the file',
            example_output='{"query": "Q3 report"}',
            context=context,
        )
        query = (params.get("query") or "").strip()
        if not query:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me what file name or keyword you want to search for.",
            )

        escaped_query = query.replace("'", "\\'")

        try:
            response = await self.request_google_api(
                "GET",
                f"{DRIVE_BASE_URL}/files",
                params={
                    "q": f"name contains '{escaped_query}'",
                    "pageSize": 10,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc, data={"query": query})

        if response.status_code == 200:
            files = response.json().get("files", [])
            if not files:
                return self.success(summary=f"No files found matching '{query}'.", data={"files": []})

            file_list = "\n".join(
                f"- {file_data['name']} - {file_data.get('webViewLink', '')}"
                for file_data in files
            )
            return self.success(
                summary=f"Search results for '{query}':\n{file_list}",
                data={"files": files},
            )

        return self.handle_google_api_error("Drive", response, data={"query": query})

    async def upload_file(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- file_path: absolute or relative path to the local file to upload
- file_name: optional name to use in Drive
- mime_type: optional MIME type
            """,
            example_output='''{
  "file_path": "C:/Users/example/Documents/report.pdf",
  "file_name": "report.pdf",
  "mime_type": "application/pdf"
}''',
            context=context,
        )

        file_path_value = (params.get("file_path") or "").strip()
        if not file_path_value:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me which local file path to upload to Drive.",
            )

        file_path = Path(file_path_value).expanduser()
        if not file_path.is_absolute():
            file_path = (Path.cwd() / file_path).resolve()

        if not file_path.exists() or not file_path.is_file():
            return self.failure(
                error="VALIDATION_ERROR",
                message=f"File not found: {file_path}",
                data={"file_path": str(file_path)},
            )

        file_name = (params.get("file_name") or file_path.name).strip() or file_path.name
        mime_type = (
            (params.get("mime_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream").strip()
        )
        metadata = {"name": file_name}

        try:
            with file_path.open("rb") as file_handle:
                files = {
                    "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
                    "file": (file_name, file_handle, mime_type),
                }
                response = await self.request_google_api(
                    "POST",
                    DRIVE_UPLOAD_URL,
                    params={
                        "uploadType": "multipart",
                        "fields": "id,name,mimeType,webViewLink",
                    },
                    files=files,
                )
        except Exception as exc:
            return self.handle_google_exception(
                "Drive",
                exc,
                data={"file_path": str(file_path), "file_name": file_name},
            )

        if response.status_code in (200, 201):
            file_data = response.json()
            return self.success(
                summary=f"Uploaded '{file_data.get('name', file_name)}' to Google Drive.",
                data={"file": file_data},
            )

        return self.handle_google_api_error(
            "Drive",
            response,
            data={"file_path": str(file_path), "file_name": file_name},
        )

    async def read_file(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- query: file name or search phrase to identify the Drive file
            """,
            example_output='{"query": "Q3 report"}',
            context=context,
        )
        query = (params.get("query") or "").strip()
        if not query:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please specify the Google Drive file you want me to read or summarize.",
            )

        match = await self._find_file_match(query)
        if isinstance(match, dict) and match.get("status") == "error":
            return match
        if not match:
            return self.success(
                summary=f"I could not find a Drive file matching '{query}'.",
                data={"query": query},
            )

        file_text_response = await self._download_file_text(match)
        if isinstance(file_text_response, dict) and file_text_response.get("status") == "error":
            return file_text_response

        if not file_text_response["content"]:
            return self.failure(
                error="UNSUPPORTED_FILE",
                message=f"I found '{match['name']}', but I could not extract readable text from that file type.",
                data={"file": match},
            )

        summary = await self.llm_complete(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_message}\n\n"
                        f"Drive file: {match['name']}\n"
                        f"MIME type: {match.get('mimeType', 'unknown')}\n\n"
                        f"File content:\n{file_text_response['content'][:6000]}"
                    ),
                }
            ],
            system_prompt=(
                "Summarize the Drive file for the user. Include key points, decisions, tasks, deadlines, "
                "and answer any explicit question from the user's request if the file text supports it."
            ),
            context=context,
        )

        return self.success(
            summary=summary,
            data={
                "file": match,
                "content_excerpt": file_text_response["content"][:3000],
            },
        )

    async def _find_file_match(self, query: str) -> Optional[Dict[str, Any]]:
        escaped_query = query.replace("'", "\\'")

        try:
            response = await self.request_google_api(
                "GET",
                f"{DRIVE_BASE_URL}/files",
                params={
                    "q": f"name contains '{escaped_query}'",
                    "pageSize": 5,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                    "orderBy": "modifiedTime desc",
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc, data={"query": query})

        if response.status_code != 200:
            return self.handle_google_api_error("Drive", response, data={"query": query})

        files = response.json().get("files", [])
        return files[0] if files else None

    async def _download_file_text(self, file_data: Dict[str, Any]) -> Dict[str, str] | Dict[str, Any]:
        file_id = file_data["id"]
        mime_type = file_data.get("mimeType", "")
        export_mime_type = None
        download_url = ""

        if mime_type == "application/vnd.google-apps.document":
            download_url = f"{DRIVE_BASE_URL}/files/{file_id}/export"
            export_mime_type = "text/plain"
        elif mime_type == "application/vnd.google-apps.spreadsheet":
            download_url = f"{DRIVE_BASE_URL}/files/{file_id}/export"
            export_mime_type = "text/csv"
        elif mime_type.startswith("text/") or mime_type in {
            "application/json",
            "application/xml",
            "text/csv",
        }:
            download_url = f"{DRIVE_BASE_URL}/files/{file_id}"
        else:
            return {"content": ""}

        try:
            response = await self.request_google_api(
                "GET",
                download_url,
                params={"mimeType": export_mime_type, "alt": None if export_mime_type else "media"},
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc, data={"file": file_data})

        if response.status_code != 200:
            return self.handle_google_api_error("Drive", response, data={"file": file_data})

        return {"content": response.text}
