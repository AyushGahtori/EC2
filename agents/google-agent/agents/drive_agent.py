"""
Google Drive Agent
File operations
"""

import json
import logging
import mimetypes
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, List, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
CACHE_TTL_SECONDS = 15 * 60
CACHE_MAX_ITEMS = 600
DEFAULT_LIST_PAGE_SIZE = 10
MAX_LIST_PAGE_SIZE = 50
LISTING_SESSION_TTL_SECONDS = 15 * 60
MAX_SESSION_SHOWN_IDS = 2500


class DriveAgent(BaseAgent):
    ACTION_ALIASES = {
        "list_files": "list",
        "get_files": "list",
        "list_documents": "list",
        "list_pdf_files": "list_pdf",
        "list_pdfs": "list_pdf",
        "pdf_list": "list_pdf",
        "list_pdf": "list_pdf",
        "list_folder_contents": "list_folder",
        "list_folder": "list_folder",
        "open_folder": "list_folder",
        "open_directory": "list_folder",
        "browse_folder": "list_folder",
        "search_files": "search",
        "find_file": "search",
        "find_files": "search",
        "read_file": "read",
        "summarize_file": "read",
        "summarize_doc": "read",
        "summarize_document": "read",
        "upload_file": "upload",
    }
    _RAM_CACHE: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def normalize_action(cls, action: str) -> str:
        cleaned = (action or "").strip().lower()
        return cls.ACTION_ALIASES.get(cleaned, cleaned)

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        forced_action = self.normalize_action(str((context or {}).get("forced_action", "")))
        if forced_action:
            action = forced_action
        else:
            action = await self._determine_action(user_message, context)

        action = self.normalize_action(action)

        if action == "list":
            return await self.list_files(user_message=user_message, context=context)
        if action == "list_pdf":
            return await self.list_files(file_filter="pdf", user_message=user_message, context=context)
        if action == "list_folder":
            return await self.list_folder_contents(user_message, context)
        if action == "search":
            return await self.search_files(user_message, context)
        if action == "upload":
            return await self.upload_file(user_message, context)
        if action == "read":
            return await self.read_file(user_message, context)
        return await self.list_files(user_message=user_message, context=context)

    async def _determine_action(
        self,
        msg: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        lower = (msg or "").lower()
        if self._looks_like_folder_navigation(lower):
            return "list_folder"
        if "pdf" in lower and any(keyword in lower for keyword in ("list", "show", "latest", "last", "recent")):
            return "list_pdf"
        if any(keyword in lower for keyword in ("summarize", "read", "open", "explain")) and (
            "file" in lower or "document" in lower or ".pdf" in lower
        ):
            return "read"

        direct_hint = self.normalize_action((msg or "").strip().split(" ", 1)[0])
        if direct_hint in {"list", "list_pdf", "list_folder", "search", "read", "upload"}:
            return direct_hint

        params = await self.extract_parameters(
            user_message=msg,
            schema_description='action: one of "list", "list_pdf", "list_folder", "search", "read", "upload"',
            example_output='{"action": "list"}',
            context=context,
        )
        return self.normalize_action(params.get("action", "list"))

    async def list_files(
        self,
        file_filter: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
        parent_folder_name: Optional[str] = None,
        page_size: int = 10,
        user_message: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requested_limit = self._extract_requested_limit(user_message)
        target_count = max(
            1,
            min(requested_limit or page_size or DEFAULT_LIST_PAGE_SIZE, MAX_LIST_PAGE_SIZE),
        )

        should_continue = self._should_continue_listing(user_message)
        session_key = self._make_listing_session_key(
            file_filter=file_filter,
            parent_folder_id=parent_folder_id,
            context=context,
        )
        session = self._load_listing_session(session_key) if should_continue else None
        if not should_continue:
            self._clear_listing_session(session_key)
        page_token = str(session.get("next_page_token", "")) or None if session else None
        shown_ids = set(session.get("shown_file_ids", [])) if session else set()

        query_parts = ["trashed = false"]
        if parent_folder_id:
            query_parts.append(f"'{parent_folder_id}' in parents")
        if file_filter == "pdf":
            query_parts.append("mimeType = 'application/pdf'")
        query = " and ".join(query_parts)

        files: List[Dict[str, Any]] = []
        next_page_token = page_token
        while len(files) < target_count:
            try:
                response = await self.request_google_api(
                    "GET",
                    f"{DRIVE_BASE_URL}/files",
                    params={
                        "pageSize": min(100, target_count + 15),
                        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,parents)",
                        "orderBy": "modifiedTime desc",
                        "q": query,
                        "pageToken": next_page_token,
                    },
                    retry_on_failure=True,
                )
            except Exception as exc:
                return self.handle_google_exception("Drive", exc)

            if response.status_code != 200:
                return self.handle_google_api_error("Drive", response)

            data = response.json()
            batch = data.get("files", [])
            next_page_token = data.get("nextPageToken")

            for item in batch:
                item_id = str(item.get("id", "")).strip()
                if item_id and item_id in shown_ids:
                    continue
                files.append(item)
                if item_id:
                    shown_ids.add(item_id)
                if len(files) >= target_count:
                    break

            if not next_page_token:
                break

        self._save_listing_session(
            session_key=session_key,
            next_page_token=next_page_token,
            shown_file_ids=list(shown_ids),
        )

        self._update_cache(files)
        if parent_folder_id:
            self._set_current_folder(parent_folder_id, parent_folder_name or "Selected folder")
        else:
            self._set_current_folder(None, None)

        if not files:
            if should_continue:
                return self.success(
                    summary="No more files left in this batch. Ask for a fresh list to restart from the top.",
                    data={
                        "files": [],
                        "hasMore": False,
                        "returnedCount": 0,
                        "totalShown": len(shown_ids),
                    },
                )
            if parent_folder_id:
                return self.success(
                    summary=f"Folder '{parent_folder_name or 'selected folder'}' is empty.",
                    data={
                        "files": [],
                        "hasMore": False,
                        "returnedCount": 0,
                        "totalShown": len(shown_ids),
                        "currentFolder": {"id": parent_folder_id, "name": parent_folder_name or "Selected folder"},
                    },
                )
            if file_filter == "pdf":
                return self.success(
                    summary="I could not find any recent PDF files in your Drive.",
                    data={"files": [], "hasMore": False, "returnedCount": 0, "totalShown": len(shown_ids)},
                )
            return self.success(
                summary="Your Google Drive is empty.",
                data={"files": [], "hasMore": False, "returnedCount": 0, "totalShown": len(shown_ids)},
            )

        file_list = "\n".join(
            f"- {file_data['name']} ({file_data.get('mimeType', 'unknown')})"
            for file_data in files
        )
        if parent_folder_id:
            header = f"Files inside folder '{parent_folder_name or 'selected folder'}':\n"
        elif file_filter == "pdf":
            header = "Your recent PDF files:\n"
        else:
            header = "Your Drive files:\n"

        payload: Dict[str, Any] = {
            "files": files,
            "hasMore": bool(next_page_token),
            "returnedCount": len(files),
            "totalShown": len(shown_ids),
            "excludedIdsCount": max(0, len(shown_ids) - len(files)),
            "cursorId": session_key,
            "nextCursor": session_key if next_page_token else None,
        }
        if parent_folder_id:
            payload["currentFolder"] = {"id": parent_folder_id, "name": parent_folder_name or "Selected folder"}
        return self.success(summary=f"{header}{file_list}", data=payload)

    async def list_folder_contents(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        folder_query = self._extract_folder_query(user_message)
        cache = self._get_cache()

        if folder_query == "__CURRENT__":
            current_folder_id = cache.get("current_folder_id")
            if not current_folder_id:
                return self.failure(error="VALIDATION_ERROR", message="Please specify which folder to open.")
            return await self.list_files(
                parent_folder_id=current_folder_id,
                parent_folder_name=cache.get("current_folder_name") or "Current folder",
                page_size=25,
                user_message=user_message,
                context=context,
            )

        if not folder_query:
            return self.failure(error="VALIDATION_ERROR", message="Please tell me which folder to open.")

        folder_match = await self._resolve_folder_match(folder_query)
        if isinstance(folder_match, dict) and folder_match.get("status") == "error":
            return folder_match
        if not folder_match:
            return self.success(
                summary=f"I could not find a folder named '{folder_query}'.",
                data={"folder": folder_query, "files": []},
            )

        return await self.list_files(
            parent_folder_id=str(folder_match["id"]),
            parent_folder_name=str(folder_match.get("name") or folder_query),
            page_size=25,
            user_message=user_message,
            context=context,
        )

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
        query = self._sanitize_query((params.get("query") or "").strip())
        if not query:
            query = self._sanitize_query(self._extract_query_from_message(user_message))
        if not query:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me what file name or keyword you want to search for.",
            )

        files_or_error = await self._search_drive_files_by_name(query, page_size=10)
        if isinstance(files_or_error, dict) and files_or_error.get("status") == "error":
            return files_or_error
        files = files_or_error
        self._update_cache(files)
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
                        "fields": "id,name,mimeType,webViewLink,parents",
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
            self._update_cache([file_data])
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
        context = context or {}
        pre_resolved_file_id = str(context.get("pre_resolved_file_id", "")).strip()
        query = self._sanitize_query(self._extract_query_from_message(user_message))

        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- query: file name or search phrase to identify the Drive file
            """,
            example_output='{"query": "Q3 report"}',
            context=context,
        )
        query = query or self._sanitize_query((params.get("query") or "").strip())

        if pre_resolved_file_id:
            match = await self._get_file_by_id(pre_resolved_file_id)
            if isinstance(match, dict) and match.get("status") == "error":
                return match
            if not match:
                return self.failure(
                    error="FILE_NOT_FOUND",
                    message="I could not access the selected Drive file. Please choose it again from a fresh Drive list.",
                    data={"file_id": pre_resolved_file_id},
                )
        elif not query:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please specify the Google Drive file you want me to read or summarize.",
            )
        else:
            match = await self._find_file_match(query, user_message=user_message)
            if isinstance(match, dict) and match.get("status") == "error":
                return match
            if not match:
                return self.success(
                    summary=f"I could not find a Drive file matching '{query}'.",
                    data={"query": query},
                )

        file_text_response = await self._download_file_text(match)
        if isinstance(file_text_response, dict) and file_text_response.get("status") in {"error", "action_required"}:
            return file_text_response

        extracted_content = str(file_text_response.get("content", "")).strip()
        if not extracted_content:
            empty_reason = str(file_text_response.get("reason", "")).strip()
            if empty_reason == "NO_TEXT_EXTRACTED":
                no_text_message = (
                    f"I found '{match['name']}', but this PDF appears to contain no selectable text "
                    "(for example, a low-quality or heavily scanned image PDF). I attempted OCR as well, "
                    "but could not recover readable text."
                )
            else:
                no_text_message = f"I found '{match['name']}', but I could not extract readable text from that file type."
            return self.failure(
                error="UNSUPPORTED_FILE",
                message=no_text_message,
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
                        f"File content:\n{extracted_content[:6000]}"
                    ),
                }
            ],
            system_prompt=(
                "Summarize the Drive file for the user. Include key points, decisions, tasks, deadlines, "
                "and answer any explicit question from the user's request if the file text supports it."
            ),
            context=context,
        )
        if (not summary) or ("I could not summarize the results due to an internal error." in summary):
            summary = self._fallback_summarize_text(extracted_content, user_message, match)

        return self.success(
            summary=summary,
            data={
                "file": match,
                "content_excerpt": extracted_content[:3000],
            },
        )

    async def _find_file_match(self, query: str, user_message: str = "") -> Optional[Dict[str, Any]]:
        sanitized_query = self._sanitize_query(query) or self._sanitize_query(user_message)
        if not sanitized_query:
            return None

        is_pdf_requested = ".pdf" in sanitized_query.lower() or "pdf" in user_message.lower()
        cache = self._get_cache()
        current_parent_id = cache.get("current_folder_id")

        cached_candidates = self._find_cached_items(
            query=sanitized_query,
            folders_only=False,
            preferred_parent_id=current_parent_id,
            prefer_pdf=is_pdf_requested,
        )
        if cached_candidates:
            return self._rank_file_matches(
                cached_candidates,
                query=sanitized_query,
                prefer_pdf=is_pdf_requested,
                preferred_parent_id=current_parent_id,
            )

        files_or_error = await self._search_drive_files_by_name(
            sanitized_query,
            page_size=10,
            parent_folder_id=current_parent_id,
            prefer_pdf=is_pdf_requested,
        )
        if isinstance(files_or_error, dict) and files_or_error.get("status") == "error":
            return files_or_error
        files = files_or_error
        if files:
            self._update_cache(files)
            return self._rank_file_matches(
                files,
                query=sanitized_query,
                prefer_pdf=is_pdf_requested,
                preferred_parent_id=current_parent_id,
            )

        if current_parent_id:
            global_files_or_error = await self._search_drive_files_by_name(
                sanitized_query,
                page_size=10,
                parent_folder_id=None,
                prefer_pdf=is_pdf_requested,
            )
            if isinstance(global_files_or_error, dict) and global_files_or_error.get("status") == "error":
                return global_files_or_error
            global_files = global_files_or_error
            if global_files:
                self._update_cache(global_files)
                return self._rank_file_matches(
                    global_files,
                    query=sanitized_query,
                    prefer_pdf=is_pdf_requested,
                )

        return None

    async def _resolve_folder_match(self, folder_query: str) -> Optional[Dict[str, Any]]:
        sanitized = self._sanitize_query(folder_query)
        if not sanitized:
            return None

        cache = self._get_cache()
        current_parent_id = cache.get("current_folder_id")
        cached_candidates = self._find_cached_items(
            query=sanitized,
            folders_only=True,
            preferred_parent_id=current_parent_id,
        )
        if cached_candidates:
            return self._rank_file_matches(
                cached_candidates,
                query=sanitized,
                prefer_folder=True,
                preferred_parent_id=current_parent_id,
            )

        files_or_error = await self._search_drive_files_by_name(
            sanitized,
            page_size=10,
            parent_folder_id=current_parent_id,
            folders_only=True,
        )
        if isinstance(files_or_error, dict) and files_or_error.get("status") == "error":
            return files_or_error
        files = files_or_error
        if files:
            self._update_cache(files)
            return self._rank_file_matches(files, query=sanitized, prefer_folder=True, preferred_parent_id=current_parent_id)

        if current_parent_id:
            global_files_or_error = await self._search_drive_files_by_name(
                sanitized,
                page_size=10,
                parent_folder_id=None,
                folders_only=True,
            )
            if isinstance(global_files_or_error, dict) and global_files_or_error.get("status") == "error":
                return global_files_or_error
            global_files = global_files_or_error
            if global_files:
                self._update_cache(global_files)
                return self._rank_file_matches(global_files, query=sanitized, prefer_folder=True)

        return None

    async def _search_drive_files_by_name(
        self,
        query: str,
        page_size: int = 10,
        parent_folder_id: Optional[str] = None,
        prefer_pdf: bool = False,
        folders_only: bool = False,
    ) -> List[Dict[str, Any]] | Dict[str, Any]:
        escaped_query = query.replace("'", "\\'")
        q_parts = ["trashed = false", f"name contains '{escaped_query}'"]
        if parent_folder_id:
            q_parts.append(f"'{parent_folder_id}' in parents")
        if prefer_pdf:
            q_parts.append("(mimeType = 'application/pdf' or name contains '.pdf')")
        if folders_only:
            q_parts.append(f"mimeType = '{FOLDER_MIME_TYPE}'")
        drive_query = " and ".join(q_parts)

        try:
            response = await self.request_google_api(
                "GET",
                f"{DRIVE_BASE_URL}/files",
                params={
                    "q": drive_query,
                    "pageSize": page_size,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink,parents)",
                    "orderBy": "modifiedTime desc",
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc, data={"query": query})

        if response.status_code != 200:
            return self.handle_google_api_error("Drive", response, data={"query": query})
        return response.json().get("files", [])

    async def _get_file_by_id(self, file_id: str) -> Optional[Dict[str, Any]] | Dict[str, Any]:
        cleaned_file_id = str(file_id or "").strip()
        if not cleaned_file_id:
            return None

        cache = self._get_cache()
        cached = cache.get("items_by_id", {}).get(cleaned_file_id)
        if isinstance(cached, dict):
            return cached

        try:
            response = await self.request_google_api(
                "GET",
                f"{DRIVE_BASE_URL}/files/{cleaned_file_id}",
                params={"fields": "id,name,mimeType,modifiedTime,webViewLink,parents"},
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Drive", exc, data={"file_id": cleaned_file_id})

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            return self.handle_google_api_error("Drive", response, data={"file_id": cleaned_file_id})

        file_data = response.json()
        self._update_cache([file_data])
        return file_data

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
        elif mime_type == "application/pdf" or str(file_data.get("name", "")).lower().endswith(".pdf"):
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

        if mime_type == "application/pdf" or str(file_data.get("name", "")).lower().endswith(".pdf"):
            pdf_content = self._extract_pdf_text(response.content)
            if not pdf_content:
                return {"content": "", "reason": "NO_TEXT_EXTRACTED"}
            return {"content": pdf_content}

        return {"content": response.text}

    def _extract_query_from_message(self, user_message: str) -> str:
        text = (user_message or "").strip()
        if not text:
            return ""

        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
        for pair in quoted:
            candidate = (pair[0] or pair[1]).strip()
            if candidate:
                return candidate

        filename_match = re.search(
            r"([A-Za-z0-9 _\-.]+\.(?:pdf|docx?|txt|md|csv|json|pptx?|xlsx?))",
            text,
            re.IGNORECASE,
        )
        if filename_match:
            return self._strip_query_leading_fillers(filename_match.group(1).strip())

        return ""

    def _extract_folder_query(self, user_message: str) -> str:
        text = (user_message or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if any(marker in lowered for marker in ("this folder", "that folder", "current folder")):
            return "__CURRENT__"

        patterns = [
            r"(?:inside|within|under|in)\s+(?:the\s+)?(.+?)\s+folder\b",
            r"(?:open|enter|go to|go inside|browse|show files in|list files in|list files inside)\s+(?:the\s+)?(.+?)\s+folder\b",
            r"(.+?)\s+folder\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip(" .,:")
        return ""

    def _sanitize_query(self, query: str) -> str:
        cleaned = (query or "").strip()
        cleaned = re.sub(
            r"^(please\s+)?(can you\s+|could you\s+)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(summarize|read|open|explain|find|search|show|list)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(the|a|an)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = self._strip_query_leading_fillers(cleaned)
        cleaned = re.sub(
            r"\b(from this|from (my\s+)?drive|in (my\s+)?drive|google drive|inside|within|under|folder)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(file|document|pdf)\s+(named|called)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:")
        return cleaned

    def _strip_query_leading_fillers(self, value: str) -> str:
        cleaned = (value or "").strip()
        cleaned = re.sub(
            r"^(please\s+)?(can you\s+|could you\s+)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(summarize|summarise|read|open|explain|find|search|show)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(?:(the|a|an|this|that|same|one|file|document|pdf)\s+)+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", cleaned).strip(" .,:")

    def _extract_requested_limit(self, user_message: str) -> Optional[int]:
        text = (user_message or "").lower()
        if not text:
            return None

        patterns = [
            r"\b(?:last|latest|recent|show|list)\s+(\d{1,3})\b",
            r"\b(\d{1,3})\s+(?:files?|documents?|items?)\b",
            r"\bnext\s+(\d{1,3})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def _should_continue_listing(self, user_message: str) -> bool:
        text = (user_message or "").lower()
        if not text:
            return False

        continuation_markers = (
            "next",
            "more",
            "another batch",
            "continue",
            "keep going",
            "not in this batch",
            "could not find",
            "couldn't find",
            "cant find",
            "can't find",
            "did not find",
            "didn't find",
            "show others",
            "show more",
        )
        return any(marker in text for marker in continuation_markers)

    def _make_listing_session_key(
        self,
        file_filter: Optional[str],
        parent_folder_id: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        scope = (file_filter or "all").strip().lower() or "all"
        parent_scope = (parent_folder_id or "root").strip() or "root"
        chat_scope = "global"
        if context and isinstance(context, dict):
            chat_id = str(context.get("chatId", "")).strip()
            if chat_id:
                chat_scope = chat_id
        return f"chat:{chat_scope}|scope:{scope}|parent:{parent_scope}"

    def _load_listing_session(self, session_key: str) -> Dict[str, Any]:
        cache = self._get_cache()
        sessions = cache.setdefault("listing_sessions", {})
        now = time.time()
        raw = sessions.get(session_key)
        if not isinstance(raw, dict):
            return {}
        expires_at = float(raw.get("expires_at", 0))
        if expires_at <= now:
            sessions.pop(session_key, None)
            cache["updated_at"] = now
            return {}
        return raw

    def _save_listing_session(
        self,
        session_key: str,
        next_page_token: Optional[str],
        shown_file_ids: List[str],
    ) -> None:
        cache = self._get_cache()
        sessions = cache.setdefault("listing_sessions", {})
        deduped_shown_ids: List[str] = []
        seen: set[str] = set()
        for item_id in shown_file_ids:
            candidate = str(item_id or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            deduped_shown_ids.append(candidate)
        if len(deduped_shown_ids) > MAX_SESSION_SHOWN_IDS:
            deduped_shown_ids = deduped_shown_ids[-MAX_SESSION_SHOWN_IDS:]

        sessions[session_key] = {
            "next_page_token": next_page_token or "",
            "shown_file_ids": deduped_shown_ids,
            "expires_at": time.time() + LISTING_SESSION_TTL_SECONDS,
        }
        cache["updated_at"] = time.time()

    def _clear_listing_session(self, session_key: str) -> None:
        cache = self._get_cache()
        sessions = cache.setdefault("listing_sessions", {})
        sessions.pop(session_key, None)
        cache["updated_at"] = time.time()

    def _looks_like_folder_navigation(self, lowered_message: str) -> bool:
        if "folder" not in lowered_message:
            return False
        return any(
            marker in lowered_message
            for marker in (
                "inside",
                "within",
                "under",
                "open",
                "enter",
                "go to",
                "go inside",
                "list files in",
                "list files inside",
                "show files in",
                "browse",
            )
        )

    def _empty_cache(self) -> Dict[str, Any]:
        return {
            "updated_at": time.time(),
            "items_by_id": {},
            "name_to_items": {},
            "last_listing": [],
            "current_folder_id": None,
            "current_folder_name": None,
            "listing_sessions": {},
        }

    def _get_cache(self) -> Dict[str, Any]:
        cache_key = self.user_id or "default_user"
        cache = self._RAM_CACHE.get(cache_key)
        now = time.time()
        if not cache or (now - float(cache.get("updated_at", 0))) > CACHE_TTL_SECONDS:
            cache = self._empty_cache()
            self._RAM_CACHE[cache_key] = cache
        return cache

    def _set_current_folder(self, folder_id: Optional[str], folder_name: Optional[str]) -> None:
        cache = self._get_cache()
        cache["current_folder_id"] = folder_id
        cache["current_folder_name"] = folder_name
        cache["updated_at"] = time.time()

    def _update_cache(self, files: List[Dict[str, Any]]) -> None:
        cache = self._get_cache()
        items_by_id = cache["items_by_id"]
        name_to_items = cache["name_to_items"]

        for raw in files:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item.setdefault("parents", [])
            file_id = str(item.get("id", "")).strip()
            if not file_id:
                continue
            items_by_id[file_id] = item

            name_key = self._normalize_name(str(item.get("name", "")))
            if name_key:
                existing = name_to_items.get(name_key, [])
                deduped = [entry for entry in existing if str(entry.get("id", "")) != file_id]
                deduped.insert(0, item)
                name_to_items[name_key] = deduped[:50]

        cache["last_listing"] = files[:50]
        cache["updated_at"] = time.time()

        if len(items_by_id) > CACHE_MAX_ITEMS:
            cache["items_by_id"] = {}
            cache["name_to_items"] = {}
            cache["last_listing"] = []
            cache["listing_sessions"] = {}

    def _find_cached_items(
        self,
        query: str,
        folders_only: bool = False,
        preferred_parent_id: Optional[str] = None,
        prefer_pdf: bool = False,
    ) -> List[Dict[str, Any]]:
        cache = self._get_cache()
        normalized_query = self._normalize_name(query)
        if not normalized_query:
            return []

        candidates: List[Dict[str, Any]] = []
        candidates.extend(cache.get("name_to_items", {}).get(normalized_query, []))

        pool = list(cache.get("last_listing", []))
        if not pool:
            pool = list(cache.get("items_by_id", {}).values())

        for item in pool:
            name = self._normalize_name(str(item.get("name", "")))
            if normalized_query and normalized_query in name:
                candidates.append(item)

        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            deduped.append(item)

        if folders_only:
            deduped = [item for item in deduped if str(item.get("mimeType", "")).lower() == FOLDER_MIME_TYPE]
        else:
            deduped = [item for item in deduped if str(item.get("mimeType", "")).lower() != FOLDER_MIME_TYPE]

        if prefer_pdf:
            pdf_items = [
                item
                for item in deduped
                if str(item.get("mimeType", "")).lower() == "application/pdf"
                or str(item.get("name", "")).lower().endswith(".pdf")
            ]
            if pdf_items:
                deduped = pdf_items + [item for item in deduped if item not in pdf_items]

        if preferred_parent_id:
            parent_matches = [
                item for item in deduped if preferred_parent_id in (item.get("parents") or [])
            ]
            if parent_matches:
                return parent_matches

        return deduped

    def _rank_file_matches(
        self,
        files: List[Dict[str, Any]],
        query: str,
        prefer_pdf: bool = False,
        prefer_folder: bool = False,
        preferred_parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_query = self._normalize_name(query)

        def score(file_data: Dict[str, Any]) -> int:
            name = self._normalize_name(str(file_data.get("name", "")))
            mime = str(file_data.get("mimeType", "")).lower()
            parents = file_data.get("parents") or []
            current = 0

            if normalized_query and name == normalized_query:
                current += 100
            if normalized_query and normalized_query in name:
                current += 40
            if prefer_pdf and ("application/pdf" in mime or name.endswith(".pdf")):
                current += 30
            if prefer_folder and mime == FOLDER_MIME_TYPE:
                current += 30
            if not prefer_folder and mime == FOLDER_MIME_TYPE:
                current -= 50
            if preferred_parent_id and preferred_parent_id in parents:
                current += 25
            if file_data.get("modifiedTime"):
                current += 5
            return current

        return max(files, key=score)

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        if not pdf_bytes:
            return ""
        try:
            from pypdf import PdfReader
        except Exception:
            logger.warning("pypdf is not available; PDF extraction is disabled.")
            return ""

        chunks: List[str] = []
        total_len = 0
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    logger.warning("PDF is encrypted and could not be decrypted with empty password.")
                    return ""

            for page in reader.pages[:30]:
                text = (page.extract_text() or "").strip()
                if not text:
                    try:
                        text = (page.extract_text(extraction_mode="layout") or "").strip()
                    except Exception:
                        text = ""
                if not text:
                    continue
                chunks.append(text)
                total_len += len(text)
                if total_len > 16000:
                    break
        except Exception as exc:
            logger.warning("Failed to extract PDF text with pypdf: %s", exc)

        merged = "\n\n".join(chunks).strip()
        if merged:
            return merged

        try:
            from pdfminer.high_level import extract_text as pdfminer_extract_text

            fallback_text = (pdfminer_extract_text(BytesIO(pdf_bytes)) or "").strip()
            if fallback_text:
                return fallback_text[:16000]
        except Exception as exc:
            logger.warning("Failed to extract PDF text with pdfminer: %s", exc)

        ocr_text = self._extract_pdf_text_with_ocr(pdf_bytes)
        if ocr_text:
            return ocr_text[:16000]

        return ""

    def _fallback_summarize_text(
        self,
        content: str,
        user_message: str,
        file_data: Dict[str, Any],
    ) -> str:
        """Deterministic summary when LLM is unavailable."""
        text = (content or "").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return f"I extracted text from '{file_data.get('name', 'the file')}', but there was no readable content to summarize."

        bullets: List[str] = []
        seen = set()
        for line in lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            bullets.append(normalized)
            if len(bullets) >= 8:
                break

        preview = "\n".join(f"- {item}" for item in bullets[:6])
        intent = "Summary"
        if "question" in user_message.lower() or "explain" in user_message.lower():
            intent = "Key extracted points"

        return (
            f"{intent} for '{file_data.get('name', 'file')}':\n"
            f"{preview}\n\n"
            "Note: Generated without LLM because the model endpoint is currently unreachable."
        )

    def _extract_pdf_text_with_ocr(self, pdf_bytes: bytes) -> str:
        """OCR fallback for scanned/image PDFs using poppler + tesseract."""
        try:
            from pdf2image import convert_from_bytes
            import pytesseract
            from PIL import ImageOps
        except Exception as exc:
            logger.warning("OCR dependencies missing (pdf2image/pytesseract): %s", exc)
            return ""

        try:
            pages = convert_from_bytes(
                pdf_bytes,
                dpi=200,
                fmt="png",
                first_page=1,
                last_page=8,
                thread_count=2,
            )
        except Exception as exc:
            logger.warning("Failed to rasterize PDF for OCR: %s", exc)
            return ""

        chunks: List[str] = []
        total_len = 0
        for image in pages:
            try:
                gray = ImageOps.grayscale(image)
                bw = gray.point(lambda p: 255 if p > 165 else 0)
                text = (pytesseract.image_to_string(bw, config="--oem 3 --psm 6") or "").strip()
                if len(text) < 24:
                    text = (pytesseract.image_to_string(gray, config="--oem 3 --psm 3") or "").strip()
            except Exception as exc:
                logger.warning("Failed OCR on a PDF page: %s", exc)
                continue

            if not text:
                continue
            chunks.append(text)
            total_len += len(text)
            if total_len > 16000:
                break

        return "\n\n".join(chunks).strip()
