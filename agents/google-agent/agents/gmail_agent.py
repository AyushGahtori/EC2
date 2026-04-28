"""
Gmail Agent
Handles Gmail operations: send, draft, reply, summarize inbox
"""

import base64
import json
import logging
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
DEFAULT_INBOX_LIST_COUNT = 10
MAX_INBOX_LIST_COUNT = 20
EMAIL_CACHE_TTL_SECONDS = 30 * 60
EMAIL_CACHE_MAX_ITEMS = 200


class GmailAgent(BaseAgent):
    """Agent for Gmail operations."""
    _RAM_EMAIL_CACHE: Dict[str, Dict[str, Any]] = {}

    ACTION_ALIASES = {
        "send_email": "send",
        "compose": "send",
        "compose_email": "send",
        "mail": "send",
        "email": "send",
        "create_draft": "draft",
        "draft_email": "draft",
        "summarize_inbox": "summarize",
        "inbox_summary": "summarize",
        "list_emails": "list",
        "inbox": "list",
        "reply_email": "reply",
        "search_emails": "search",
        "read_email": "read",
        "summarize_email": "read",
        "summarise_email": "read",
        "email_summary": "read",
        "summarize_message": "read",
        "mark_as_read": "mark_read",
        "mark_email_as_read": "mark_read",
    }

    @classmethod
    def normalize_action(cls, action: str) -> str:
        cleaned = (action or "").strip().lower()
        return cls.ACTION_ALIASES.get(cleaned, cleaned)

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Determine action and execute Gmail operation."""
        context = context or {}
        self._hydrate_email_cache_from_context(context)
        pending_task = self._get_pending_task(context)
        forced_action = self.normalize_action(str((context or {}).get("forced_action", "")))

        if forced_action:
            action = forced_action
        elif pending_task and self._looks_like_follow_up_message(user_message, pending_task):
            action = pending_task.get("action", "send")
        else:
            action = await self._determine_action(user_message, context)

        action = self.normalize_action(action)
        logger.info(f"[gmail] action: {action}")

        if action == "send":
            return await self.send_email(user_message, context)
        if action == "draft":
            return await self.draft_email(user_message, context)
        if action == "summarize":
            return await self.summarize_inbox(user_message)
        if action == "reply":
            return await self.reply_email(user_message)
        if action == "list":
            return await self.list_emails(user_message)
        if action == "search":
            return await self.search_emails(user_message, context)
        if action == "read":
            return await self.read_email(user_message, context)
        if action in {"mark_read", "mark_as_read"}:
            return await self.mark_email_as_read(user_message, context)
        return await self.list_emails()

    async def _determine_action(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        direct_hint = self.normalize_action((user_message or "").strip().split(" ", 1)[0])
        if direct_hint in {"send", "draft", "summarize", "reply", "list", "search", "read", "mark_read"}:
            return direct_hint

        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='action: one of "send", "draft", "summarize", "reply", "list", "search", "read", "mark_read"',
            example_output='{"action": "send"}',
            context=context,
        )
        return self.normalize_action(params.get("action", "list"))

    async def send_email(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send an email."""
        params = await self._resolve_email_params(user_message, context, action="send")
        missing_fields = self._get_missing_fields(params, required_fields=["to", "subject", "body"])
        if missing_fields:
            return self._needs_more_email_details("send", params, missing_fields)

        to = params["to"]
        subject = params["subject"]
        body = params["body"]

        message = MIMEMultipart()
        message["To"] = to
        message["Subject"] = subject
        if params.get("cc"):
            message["Cc"] = ", ".join(params["cc"])
        message.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            response = await self.request_google_api(
                "POST",
                f"{GMAIL_BASE_URL}/messages/send",
                json={"raw": raw},
            )
        except Exception as exc:
            result = self.handle_google_exception("Gmail", exc, data={"params": params})
            result["clear_pending_task"] = True
            return result

        if response.status_code == 200:
            result = self.success(
                summary=f"Email sent to {to} with subject: '{subject}'",
                data={"message_id": response.json().get("id"), "params": params},
            )
            result["clear_pending_task"] = True
            return result

        result = self.handle_google_api_error("Gmail", response, data={"params": params})
        result["clear_pending_task"] = True
        return result

    async def draft_email(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a draft email."""
        params = await self._resolve_email_params(user_message, context, action="draft")
        missing_fields = self._get_missing_fields(params, required_fields=["subject", "body"])
        if missing_fields:
            return self._needs_more_email_details("draft", params, missing_fields)

        to = params.get("to", "")
        subject = params["subject"]
        body = params["body"]

        message = MIMEMultipart()
        if to:
            message["To"] = to
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            response = await self.request_google_api(
                "POST",
                f"{GMAIL_BASE_URL}/drafts",
                json={"message": {"raw": raw}},
            )
        except Exception as exc:
            result = self.handle_google_exception("Gmail", exc, data={"params": params})
            result["clear_pending_task"] = True
            return result

        if response.status_code == 200:
            result = self.success(
                summary=f"Draft created for {to or 'the requested recipient'}: '{subject}'",
                data={"draft": response.json(), "params": params},
            )
            result["clear_pending_task"] = True
            return result

        result = self.handle_google_api_error("Gmail", response, data={"params": params})
        result["clear_pending_task"] = True
        return result

    async def summarize_inbox(self, user_message: str = "") -> Dict[str, Any]:
        """Fetch and summarize recent emails."""
        requested_limit = self._extract_requested_limit(user_message)
        target_count = max(
            1,
            min(requested_limit or DEFAULT_INBOX_LIST_COUNT, MAX_INBOX_LIST_COUNT),
        )

        try:
            list_response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages",
                params={"maxResults": target_count, "labelIds": "INBOX"},
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Gmail", exc)

        if list_response.status_code != 200:
            return self.handle_google_api_error("Gmail", list_response)

        messages = list_response.json().get("messages", [])
        email_summaries = []

        for msg in messages[:target_count]:
            try:
                detail_response = await self.request_google_api(
                    "GET",
                    f"{GMAIL_BASE_URL}/messages/{msg['id']}",
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    retry_on_failure=True,
                )
            except Exception as exc:
                return self.handle_google_exception("Gmail", exc)

            if detail_response.status_code == 401:
                return self.handle_google_api_error("Gmail", detail_response)

            if detail_response.status_code == 200:
                detail = detail_response.json()
                headers = {
                    header["name"]: header["value"]
                    for header in detail.get("payload", {}).get("headers", [])
                }
                email_summaries.append(
                    {
                        "id": msg["id"],
                        "from": headers.get("From", "Unknown"),
                        "subject": headers.get("Subject", "No Subject"),
                        "date": headers.get("Date", "Unknown"),
                        "snippet": detail.get("snippet", ""),
                    }
                )

        summary_text = "\n".join(
            f"- From: {email_item['from'][:30]} | {email_item['subject'][:50]}"
            for email_item in email_summaries
        ) or "No recent emails found"

        self._update_email_cache(email_summaries)

        return self.success(
            summary=f"Recent inbox ({len(email_summaries)} emails):\n{summary_text}",
            data={
                "emails": email_summaries,
                "returnedCount": len(email_summaries),
                "requestedCount": target_count,
            },
        )

    async def list_emails(self, user_message: str = "") -> Dict[str, Any]:
        """List recent emails."""
        return await self.summarize_inbox(user_message)

    async def reply_email(self, user_message: str) -> Dict[str, Any]:
        """Reply to an email (requires thread context)."""
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- reply_to_subject: subject of email to reply to
- reply_body: your reply content
            """,
            example_output='{"reply_to_subject": "Project Update", "reply_body": "Thanks for the update!"}',
        )
        return self.success(
            summary=(
                f"[Reply drafted] To respond to '{params.get('reply_to_subject', 'email')}': "
                f"{params.get('reply_body', '')}. Full reply requires selecting the specific thread."
            ),
            data={"params": params, "note": "Full reply requires thread ID selection"},
        )

    async def search_emails(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- query: Gmail search query to find messages
- count: maximum number of results to return (default 10, max 20)
            """,
            example_output='{"query": "from:alice budget", "count": 5}',
            context=context,
        )

        query = self._clean_text_value(str(params.get("query", "")))
        count = min(max(int(params.get("count", 10) or 10), 1), 20)
        if not query:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me what email to search for.",
            )

        messages = await self._fetch_message_metadata_list(max_results=count, query=query)
        if isinstance(messages, dict) and messages.get("status") == "error":
            return messages

        email_summaries = messages.get("emails", [])
        if not email_summaries:
            return self.success(
                summary=f"No Gmail messages matched '{query}'.",
                data={"emails": [], "query": query},
            )

        summary_text = "\n".join(
            f"- {item['subject']} — {item['from']}"
            for item in email_summaries
        )
        self._update_email_cache(email_summaries)
        return self.success(
            summary=f"Found {len(email_summaries)} Gmail messages for '{query}':\n{summary_text}",
            data={"emails": email_summaries, "query": query},
        )

    async def read_email(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        strict_resolution = bool((context or {}).get("strict_resolution"))
        # Check for a pre-resolved message_id from orchestrator deterministic routing
        pre_resolved_id = self._clean_text_value(
            str((context or {}).get("pre_resolved_message_id", ""))
        )

        if pre_resolved_id:
            message_id = pre_resolved_id
            query = user_message
        else:
            if strict_resolution:
                return {
                    "status": "needs_input",
                    "agent": self.agent_name,
                    "summary": "I need the exact Gmail message ID from the orchestrator before reading this email.",
                    "data": {
                        "missing_fields": ["message_id"],
                        "query": user_message,
                        "resolution_required": True,
                    },
                }
            params = await self.extract_parameters(
                user_message=user_message,
                schema_description="""
- message_id: exact Gmail message id if known
- query: Gmail search query or description of the message to read
                """,
                example_output='{"message_id": null, "query": "from:alice budget"}',
                context=context,
            )

            message_id = self._clean_text_value(str(params.get("message_id", "")))
            query = self._clean_text_value(str(params.get("query", "")))

        if not message_id:
            cached_match = await self._find_cached_message_match(query=query or user_message, context=context)
            if cached_match:
                message_id = str(cached_match["id"])

        if not message_id:
            message_match = await self._find_message_match(query=query or user_message, context=context)
            if isinstance(message_match, dict) and message_match.get("status") == "error":
                return message_match
            if not message_match:
                return self.success(
                    summary="I could not find a matching Gmail message to read.",
                    data={"query": query or user_message},
                )
            message_id = message_match["id"]

        detail_response = await self._get_message_details(message_id)
        if isinstance(detail_response, dict) and detail_response.get("status") == "error":
            return detail_response

        detail = detail_response
        headers = self._extract_headers(detail)
        body = self._extract_message_body(detail.get("payload", {}))
        snippet = detail.get("snippet", "")
        raw_text_for_summary = body or snippet or "No readable body was available."
        text_for_summary = self._clean_email_body_for_summary(raw_text_for_summary)

        summary = await self.llm_complete(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_message}\n\n"
                        f"From: {headers.get('From', 'Unknown')}\n"
                        f"Subject: {headers.get('Subject', 'No Subject')}\n"
                        f"Date: {headers.get('Date', 'Unknown')}\n\n"
                        f"Email content:\n{text_for_summary[:7000]}"
                    ),
                }
            ],
            system_prompt=(
                "You summarize Gmail messages for a busy user. Ignore unsubscribe text, tracking links, "
                "social links, legal footers, navigation, and boilerplate. Extract the useful content: "
                "what happened, important numbers/details, asks, deadlines, and follow-up. If the email "
                "is a newsletter or report, summarize the actual metrics or takeaways rather than saying "
                "it is informational. Return this exact plain-text structure:\n"
                "Sender: <sender>\n"
                "Subject: <subject>\n"
                "Summary: <one useful sentence>\n"
                "Key points:\n"
                "- <point>\n"
                "- <point>\n"
                "Follow-up: <action or None needed>"
            ),
            context=context,
        )
        summary = self._coerce_message_summary(
            summary=summary,
            headers=headers,
            body=text_for_summary,
            snippet=snippet,
        )

        self._update_email_cache(
            [
                {
                    "id": detail.get("id"),
                    "from": headers.get("From", "Unknown"),
                    "subject": headers.get("Subject", "No Subject"),
                    "date": headers.get("Date", "Unknown"),
                    "snippet": snippet,
                }
            ]
        )

        return self.success(
            summary=summary,
            data={
                "id": detail.get("id"),
                "threadId": detail.get("threadId"),
                "from": headers.get("From", "Unknown"),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "No Subject"),
                "date": headers.get("Date", "Unknown"),
                "body": text_for_summary[:3000],
                "labels": detail.get("labelIds", []),
            },
        )

    def _coerce_message_summary(
        self,
        summary: str,
        headers: Dict[str, str],
        body: str,
        snippet: str,
    ) -> str:
        cleaned_summary = (summary or "").strip()
        if cleaned_summary and cleaned_summary.lower() != "i could not summarize the results due to an internal error.":
            return cleaned_summary

        source_text = (body or snippet or "No readable body was available.").strip()
        source_text = re.sub(r"\s+", " ", source_text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", source_text)
        useful_sentences = [line.strip(" -") for line in sentences if line.strip()]
        summary_line = useful_sentences[0] if useful_sentences else source_text
        bullet_lines = useful_sentences[1:3]

        fallback_lines = [
            f"Sender: {headers.get('From', 'Unknown')}",
            f"Subject: {headers.get('Subject', 'No Subject')}",
            f"Summary: {summary_line[:240] if summary_line else 'No readable body was available.'}",
            "Key points:",
        ]
        if bullet_lines:
            fallback_lines.extend(f"- {line[:220]}" for line in bullet_lines)
        else:
            fallback_lines.append(f"- {source_text[:220] if source_text else 'No readable body was available.'}")
        fallback_lines.append("Follow-up: None needed")
        return "\n".join(fallback_lines)

    async def mark_email_as_read(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        strict_resolution = bool((context or {}).get("strict_resolution"))
        pre_resolved_id = self._clean_text_value(
            str((context or {}).get("pre_resolved_message_id", ""))
        )

        if pre_resolved_id:
            message_id = pre_resolved_id
            query = user_message
        else:
            if strict_resolution:
                return {
                    "status": "needs_input",
                    "agent": self.agent_name,
                    "summary": "I need the exact Gmail message ID from the orchestrator before marking this email as read.",
                    "data": {
                        "missing_fields": ["message_id"],
                        "query": user_message,
                        "resolution_required": True,
                    },
                }
            params = await self.extract_parameters(
                user_message=user_message,
                schema_description="""
- message_id: exact Gmail message id if known
- query: Gmail search query or description of the message to mark as read
            """,
                example_output='{"message_id": null, "query": "from:alice budget"}',
                context=context,
            )

            message_id = self._clean_text_value(str(params.get("message_id", "")))
            query = self._clean_text_value(str(params.get("query", "")))

        if not message_id:
            cached_match = await self._find_cached_message_match(query=query or user_message, context=context)
            if cached_match:
                message_id = str(cached_match["id"])

        if not message_id:
            message_match = await self._find_message_match(query=query or user_message, context=context)
            if isinstance(message_match, dict) and message_match.get("status") == "error":
                return message_match
            if not message_match:
                return self.success(
                    summary="I could not find a matching Gmail message to mark as read.",
                    data={"query": query or user_message},
                )
            message_id = message_match["id"]

        try:
            response = await self.request_google_api(
                "POST",
                f"{GMAIL_BASE_URL}/messages/{message_id}/modify",
                json={"removeLabelIds": ["UNREAD"]},
            )
        except Exception as exc:
            return self.handle_google_exception("Gmail", exc, data={"message_id": message_id})

        if response.status_code == 200:
            return self.success(
                summary="Marked the Gmail message as read.",
                data={"message_id": message_id},
            )

        return self.handle_google_api_error("Gmail", response, data={"message_id": message_id})

    async def _fetch_message_metadata_list(
        self,
        max_results: int = 10,
        query: str = "",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        else:
            params["labelIds"] = "INBOX"

        try:
            list_response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages",
                params=params,
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Gmail", exc, data={"query": query})

        if list_response.status_code != 200:
            return self.handle_google_api_error("Gmail", list_response, data={"query": query})

        messages = list_response.json().get("messages", [])
        email_summaries = []

        for msg in messages[:max_results]:
            detail_response = await self._get_message_details(msg["id"], format_type="metadata")
            if isinstance(detail_response, dict) and detail_response.get("status") == "error":
                return detail_response

            headers = self._extract_headers(detail_response)
            email_summaries.append(
                {
                    "id": msg["id"],
                    "from": headers.get("From", "Unknown"),
                    "subject": headers.get("Subject", "No Subject"),
                    "date": headers.get("Date", "Unknown"),
                    "snippet": detail_response.get("snippet", ""),
                    "isUnread": "UNREAD" in (detail_response.get("labelIds", []) or []),
                }
            )

        self._update_email_cache(email_summaries)
        return {"emails": email_summaries}

    async def _find_message_match(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        result = await self._fetch_message_metadata_list(max_results=10, query=query)
        if result.get("status") == "error":
            return result

        emails = result.get("emails", [])
        if emails:
            if len(emails) == 1:
                self._set_last_selected_email(emails[0].get("id", ""))
                return emails[0]

            selected = await self._find_cached_message_match(query=query, context=context)
            if selected:
                return selected

            self._set_last_selected_email(emails[0].get("id", ""))
            return emails[0]

        if query:
            fallback = await self._fetch_message_metadata_list(max_results=10, query="")
            if fallback.get("status") == "error":
                return fallback

            fallback_emails = fallback.get("emails", [])
            if not fallback_emails:
                return None

            selected = await self._find_cached_message_match(query=query, context=context)
            if selected:
                return selected

            self._set_last_selected_email(fallback_emails[0].get("id", ""))
            return fallback_emails[0]

        return None

    async def _get_message_details(
        self,
        message_id: str,
        format_type: str = "full",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"format": format_type}
        if format_type == "metadata":
            params["metadataHeaders"] = ["From", "Subject", "Date", "To"]

        try:
            response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages/{message_id}",
                params=params,
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Gmail", exc, data={"message_id": message_id})

        if response.status_code != 200:
            return self.handle_google_api_error("Gmail", response, data={"message_id": message_id})

        return response.json()

    def _extract_headers(self, detail: Dict[str, Any]) -> Dict[str, str]:
        return {
            header["name"]: header["value"]
            for header in detail.get("payload", {}).get("headers", [])
        }

    def _extract_message_body(self, payload: Dict[str, Any]) -> str:
        if payload.get("body", {}).get("data"):
            return self._decode_body(payload["body"]["data"])

        parts = payload.get("parts") or []
        for mime_type in ("text/plain", "text/html"):
            for part in parts:
                if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
                    body = self._decode_body(part["body"]["data"])
                    if mime_type == "text/html":
                        body = re.sub(r"<[^>]+>", " ", body)
                        body = re.sub(r"\s+", " ", body).strip()
                    return body

        for part in parts:
            nested_parts = part.get("parts") or []
            if nested_parts:
                body = self._extract_message_body(part)
                if body:
                    return body

        return ""

    def _clean_email_body_for_summary(self, body: str) -> str:
        text = body or ""
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"<https?://[^>]+>", " ", text)
        text = re.sub(r"\[[^\]]*\]\(https?://[^)]+\)", " ", text)
        text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)

        footer_patterns = [
            r"\bView Web Version\b",
            r"\bEmail Preferences\b",
            r"\bUnsubscribe\b",
            r"\bManage your notification\b",
            r"\bThis email was sent\b",
            r"\bYou are receiving this email\b",
            r"\bPrivacy Policy\b",
        ]
        cut_at = len(text)
        for pattern in footer_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                cut_at = min(cut_at, match.start())
        text = text[:cut_at]

        lines = []
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            if re.fullmatch(r"[•·|\\/\-_\s]+", line):
                continue
            if line.lower() in {"facebook", "instagram", "linkedin", "x", "twitter"}:
                continue
            lines.append(line)

        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned or "No readable body was available."

    def _decode_body(self, encoded_body: str) -> str:
        padding = len(encoded_body) % 4
        if padding:
            encoded_body += "=" * (4 - padding)

        try:
            return base64.urlsafe_b64decode(encoded_body.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    async def _resolve_email_params(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]],
        action: str,
    ) -> Dict[str, Any]:
        params = {"to": "", "subject": "", "body": "", "cc": []}
        pending_task = self._get_pending_task(context, action=action)
        agent_outputs = self._get_agent_outputs(context, pending_task)

        if pending_task:
            params = self._merge_email_params(params, pending_task.get("params"))

        extracted = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- to: recipient email address
- subject: email subject
- body: email body content
- cc: optional CC addresses (list)
            """,
            example_output='''{
  "to": "john@example.com",
  "subject": "Meeting Tomorrow",
  "body": "Hi John, just confirming our meeting tomorrow at 2pm.",
  "cc": []
}''',
            context=context,
        )
        params = self._merge_email_params(params, extracted)
        params = self._merge_email_params(params, self._parse_email_fields(user_message))

        if pending_task:
            params = self._merge_email_params(
                params,
                self._interpret_follow_up_message(user_message, pending_task),
            )

        normalized = self._normalize_email_params(params)
        return self._apply_agent_output_hints(normalized, user_message, agent_outputs)

    def _get_pending_task(
        self,
        context: Optional[Dict[str, Any]],
        action: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        pending_task = (context or {}).get("pending_task")
        if not pending_task:
            return None
        if pending_task.get("agent") != "gmail":
            return None
        if action and pending_task.get("action") != action:
            return None
        return pending_task

    def _get_agent_outputs(
        self,
        context: Optional[Dict[str, Any]],
        pending_task: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        outputs = dict((context or {}).get("agent_outputs") or {})
        if pending_task and pending_task.get("agent_outputs"):
            outputs.update(pending_task["agent_outputs"])
        return outputs

    def _looks_like_follow_up_message(self, user_message: str, pending_task: Dict[str, Any]) -> bool:
        if not pending_task.get("missing_fields"):
            return False

        lower_message = user_message.lower()
        explicit_new_action_keywords = [
            "summarize",
            "inbox",
            "list emails",
            "draft email",
            "send email",
            "another email",
            "new email",
            "email to",
            "reply to",
        ]
        if any(keyword in lower_message for keyword in explicit_new_action_keywords):
            return False

        if any(field in lower_message for field in ("subject", "body", "recipient", "to:", "cc:", "@")):
            return True

        return len(user_message.split()) <= 18

    def _parse_email_fields(self, text: str) -> Dict[str, Any]:
        parsed: Dict[str, Any] = {}

        to_match = re.search(
            r"\bto\s*[:\-]?\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
            text,
            re.IGNORECASE,
        )
        if to_match:
            parsed["to"] = to_match.group(1)

        cc_match = re.search(r"\bcc\s*[:\-]?\s*([^\n]+)", text, re.IGNORECASE)
        if cc_match:
            parsed["cc"] = self._extract_email_addresses(cc_match.group(1))

        subject_patterns = [
            r"\bsubject(?:\s+line)?\s*(?:is|as)?\s*[:\-]\s*(.+?)(?=\s+(?:and\s+)?(?:body|message|content)\b|$)",
            r"\bsubject(?:\s+line)?\s+(?:is\s+|as\s+)?(.+?)(?=\s+(?:and\s+)?(?:body|message|content)\b|$)",
        ]
        for pattern in subject_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                parsed["subject"] = self._clean_text_value(match.group(1).rstrip(",."))
                break

        body_patterns = [
            r"\bbody\s*(?:should\s+(?:say|be|include)|is)?\s*[:\-]?\s*(.+)$",
            r"\bmessage\s*(?:should\s+(?:say|be|include)|is)?\s*[:\-]?\s*(.+)$",
            r"\bcontent\s*(?:should\s+(?:say|be|include)|is)?\s*[:\-]?\s*(.+)$",
        ]
        for pattern in body_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                parsed["body"] = self._clean_text_value(match.group(1))
                break

        if not parsed.get("to"):
            email_addresses = self._extract_email_addresses(text)
            if len(email_addresses) == 1:
                parsed["to"] = email_addresses[0]

        return parsed

    def _interpret_follow_up_message(self, user_message: str, pending_task: Dict[str, Any]) -> Dict[str, Any]:
        missing_fields = pending_task.get("missing_fields", [])
        explicit_fields = self._parse_email_fields(user_message)
        if explicit_fields:
            return explicit_fields

        if len(missing_fields) != 1:
            return {}

        missing_field = missing_fields[0]
        cleaned_message = user_message.strip()
        if not cleaned_message:
            return {}

        if missing_field == "to":
            email_addresses = self._extract_email_addresses(cleaned_message)
            if len(email_addresses) == 1:
                return {"to": email_addresses[0]}
            return {}

        if missing_field == "subject":
            return {"subject": self._clean_text_value(cleaned_message)}

        if missing_field == "body":
            return {"body": cleaned_message}

        return {}

    def _merge_email_params(self, base: Dict[str, Any], incoming: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(base)
        if not isinstance(incoming, dict):
            return merged

        for field in ("to", "subject", "body"):
            value = incoming.get(field)
            if value is None:
                continue
            if isinstance(value, list):
                value = next((item for item in value if item), "")
            cleaned = self._clean_text_value(str(value))
            if cleaned:
                merged[field] = cleaned

        cc_value = incoming.get("cc")
        if cc_value:
            if isinstance(cc_value, str):
                merged["cc"] = self._extract_email_addresses(cc_value)
            elif isinstance(cc_value, list):
                merged["cc"] = self._extract_email_addresses(" ".join(str(item) for item in cc_value))

        return merged

    def _normalize_email_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        normalized = {
            "to": self._clean_text_value(str(params.get("to", ""))),
            "subject": self._clean_text_value(str(params.get("subject", ""))),
            "body": params.get("body", "").strip(),
            "cc": [],
        }

        cc_value = params.get("cc", [])
        cc_text = " ".join(str(item) for item in cc_value) if isinstance(cc_value, list) else str(cc_value)
        cc_addresses = self._extract_email_addresses(cc_text)
        normalized["cc"] = [email for email in cc_addresses if email != normalized["to"]]
        return normalized

    def _apply_agent_output_hints(
        self,
        params: Dict[str, Any],
        user_message: str,
        agent_outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not params.get("to"):
            contextual_sender = self._get_contextual_sender_email(user_message)
            if contextual_sender:
                params["to"] = contextual_sender

        meet_data = agent_outputs.get("meet") or {}
        meet_link = meet_data.get("meet_link")
        if not meet_link:
            return params

        body = params.get("body", "")
        lower_body = body.lower()
        lower_message = user_message.lower()
        link_markers = [
            "meet same link",
            "meeting link",
            "meet link",
            "same link",
            "join link",
            "link should come",
        ]

        if any(marker in lower_body for marker in link_markers) or any(marker in lower_message for marker in link_markers):
            cleaned_body = body or user_message.strip()
            cleaned_body = re.sub(r"and here the meeting link should come", "", cleaned_body, flags=re.IGNORECASE)
            cleaned_body = re.sub(r"include the meet same link", "", cleaned_body, flags=re.IGNORECASE)
            cleaned_body = cleaned_body.strip().rstrip(":")
            params["body"] = (cleaned_body + "\n" if cleaned_body else "") + meet_link
        elif body and meet_link not in body and any(keyword in lower_message for keyword in ["join", "meet", "meeting"]):
            params["body"] = body.rstrip() + f"\n\nMeet link: {meet_link}"

        return params

    def _get_missing_fields(self, params: Dict[str, Any], required_fields: List[str]) -> List[str]:
        return [field for field in required_fields if not str(params.get(field, "")).strip()]

    def _needs_more_email_details(
        self,
        action: str,
        params: Dict[str, Any],
        missing_fields: List[str],
    ) -> Dict[str, Any]:
        field_labels = {
            "to": "recipient email address",
            "subject": "subject line",
            "body": "email body",
        }

        if len(missing_fields) == 1:
            field = missing_fields[0]
            if field == "to":
                question = f"I'm ready to {action} that email. What recipient email address should I use?"
            elif field == "subject":
                question = f"I'm ready to {action} that email. What should the subject line be?"
            else:
                question = f"I'm ready to {action} that email. What should the email body say?"
        else:
            readable_fields = [field_labels[field] for field in missing_fields]
            if len(readable_fields) == 2:
                fields_text = " and ".join(readable_fields)
            else:
                fields_text = ", ".join(readable_fields[:-1]) + f", and {readable_fields[-1]}"
            question = f"I'm ready to {action} that email, but I still need the {fields_text}."

        known_bits = []
        if params.get("to"):
            known_bits.append(f"recipient {params['to']}")
        if params.get("subject"):
            known_bits.append(f"subject '{params['subject']}'")
        if known_bits:
            question += " I already have the " + " and ".join(known_bits) + "."

        return {
            "status": "needs_input",
            "agent": self.agent_name,
            "summary": question,
            "data": {"params": params, "missing_fields": missing_fields},
            "pending_task": {
                "agent": "gmail",
                "action": action,
                "params": params,
                "missing_fields": missing_fields,
            },
        }

    def _extract_email_addresses(self, text: str) -> List[str]:
        email_addresses = EMAIL_REGEX.findall(text or "")
        seen = set()
        unique_addresses = []
        for email_address in email_addresses:
            normalized = email_address.strip().strip(".,;")
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_addresses.append(normalized)
        return unique_addresses

    def _clean_text_value(self, value: str) -> str:
        return value.strip().strip("\"'").strip()

    def _extract_requested_limit(self, user_message: str) -> Optional[int]:
        text = (user_message or "").lower()
        if not text:
            return None

        patterns = [
            r"\b(?:last|latest|recent|show|list)\s+(\d{1,3})\b",
            r"\b(\d{1,3})\s+(?:emails?|mails?|messages?)\b",
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

    def _cache_key(self) -> str:
        user_key = self._clean_text_value(str(self.user_id or "default_user")) or "default_user"
        return user_key

    def _empty_email_cache(self) -> Dict[str, Any]:
        return {"updated_at": 0.0, "emails": [], "last_selected_id": ""}

    def _get_email_cache(self) -> Dict[str, Any]:
        cache_key = self._cache_key()
        cache = self._RAM_EMAIL_CACHE.get(cache_key)
        now = time.time()

        if not isinstance(cache, dict):
            cache = self._empty_email_cache()
            self._RAM_EMAIL_CACHE[cache_key] = cache
            return cache

        updated_at = cache.get("updated_at", 0.0)
        try:
            updated_at = float(updated_at)
        except (TypeError, ValueError):
            updated_at = 0.0

        if now - updated_at > EMAIL_CACHE_TTL_SECONDS:
            cache = self._empty_email_cache()
            self._RAM_EMAIL_CACHE[cache_key] = cache
            return cache

        if not isinstance(cache.get("emails"), list):
            cache["emails"] = []
        if not isinstance(cache.get("last_selected_id"), str):
            cache["last_selected_id"] = ""

        return cache

    def _get_cached_listed_emails(self) -> List[Dict[str, Any]]:
        cache = self._get_email_cache()
        return list(cache.get("emails", []))

    def _hydrate_email_cache_from_context(self, context: Optional[Dict[str, Any]]) -> None:
        """Rebuild short-term Gmail memory from the orchestrator's persisted task context."""
        if not context:
            return

        collected: List[Dict[str, Any]] = []
        agent_outputs = context.get("agent_outputs") or {}
        if isinstance(agent_outputs, dict):
            gmail_output = agent_outputs.get("gmail") or {}
            if isinstance(gmail_output, dict) and isinstance(gmail_output.get("emails"), list):
                collected.extend([item for item in gmail_output["emails"] if isinstance(item, dict)])

        for task in context.get("recent_tasks") or []:
            if not isinstance(task, dict):
                continue
            output = task.get("output") or {}
            if isinstance(output, dict) and isinstance(output.get("emails"), list):
                collected.extend([item for item in output["emails"] if isinstance(item, dict)])

        if collected:
            self._update_email_cache(collected)

    def _update_email_cache(self, emails: Optional[List[Dict[str, Any]]]) -> None:
        if not emails:
            return

        cache = self._get_email_cache()
        now = time.time()
        normalized_new: List[Dict[str, Any]] = []

        for item in emails:
            if not isinstance(item, dict):
                continue

            message_id = self._clean_text_value(str(item.get("id", "")))
            if not message_id:
                continue

            normalized_new.append(
                {
                    "id": message_id,
                    "from": self._clean_text_value(str(item.get("from", "Unknown"))) or "Unknown",
                    "subject": self._clean_text_value(str(item.get("subject", "No Subject"))) or "No Subject",
                    "date": self._clean_text_value(str(item.get("date", "Unknown"))) or "Unknown",
                    "snippet": self._clean_text_value(str(item.get("snippet", ""))),
                    "isUnread": bool(item.get("isUnread", False)),
                    "seenAt": now,
                }
            )

        if not normalized_new:
            return

        merged: List[Dict[str, Any]] = []
        seen_ids = set()
        for candidate in normalized_new + cache.get("emails", []):
            candidate_id = self._clean_text_value(str(candidate.get("id", "")))
            if not candidate_id or candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            merged.append(candidate)
            if len(merged) >= EMAIL_CACHE_MAX_ITEMS:
                break

        cache["emails"] = merged
        cache["updated_at"] = now
        if cache.get("last_selected_id") and cache["last_selected_id"] not in seen_ids:
            cache["last_selected_id"] = ""
        self._RAM_EMAIL_CACHE[self._cache_key()] = cache

    def _set_last_selected_email(self, message_id: str) -> None:
        clean_id = self._clean_text_value(str(message_id))
        if not clean_id:
            return
        cache = self._get_email_cache()
        cache["last_selected_id"] = clean_id
        cache["updated_at"] = time.time()
        self._RAM_EMAIL_CACHE[self._cache_key()] = cache

    def _is_contextual_email_reference(self, query: str) -> bool:
        lower = (query or "").lower()
        return any(
            marker in lower
            for marker in [
                "this mail",
                "this email",
                "this message",
                "this one",
                "that mail",
                "that email",
                "that message",
                "that one",
                "last mail",
                "last email",
                "latest mail",
                "latest email",
                "first mail",
                "first email",
                "first one",
                "first male",
                "1st mail",
                "1st email",
                "same mail",
                "same email",
                "same one",
            ]
        )

    def _is_contextual_person_reference(self, query: str) -> bool:
        lower = (query or "").lower()
        return any(
            marker in lower
            for marker in [
                "this person",
                "that person",
                "same person",
                "this sender",
                "that sender",
                "sender",
                "him",
                "her",
                "them",
                "again",
            ]
        )

    def _get_contextual_cached_message(self, query: str = "") -> Optional[Dict[str, Any]]:
        cache = self._get_email_cache()
        emails = list(cache.get("emails", []))
        if not emails:
            return None

        lower = (query or "").lower()
        last_selected_id = self._clean_text_value(str(cache.get("last_selected_id", "")))

        ordinal_patterns = [
            (0, [r"\b(first|1st|#1|number 1)\b"]),
            (1, [r"\b(second|2nd|#2|number 2)\b"]),
            (2, [r"\b(third|3rd|#3|number 3)\b"]),
            (3, [r"\b(fourth|4th|#4|number 4)\b"]),
            (4, [r"\b(fifth|5th|#5|number 5)\b"]),
        ]
        for index, patterns in ordinal_patterns:
            if index < len(emails) and any(re.search(pattern, lower) for pattern in patterns):
                return emails[index]

        if "last email" in lower or "last mail" in lower:
            # The cache is newest-first, so "last/latest" means the first row the user just saw.
            return emails[0]

        if last_selected_id and any(
            marker in lower
            for marker in ["same", "this", "that", "it", "again", "reply", "respond"]
        ):
            for email in emails:
                if str(email.get("id", "")) == last_selected_id:
                    return email

        if self._is_contextual_email_reference(query):
            return emails[0]

        return None

    def _get_contextual_sender_email(self, query: str = "") -> str:
        if not self._is_contextual_person_reference(query):
            return ""

        email = self._get_contextual_cached_message(query)
        if not email:
            return ""

        sender = str(email.get("from", ""))
        addresses = self._extract_email_addresses(sender)
        return addresses[0] if addresses else ""

    async def _find_cached_message_match(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        cache = self._get_email_cache()
        emails = list(cache.get("emails", []))
        if not emails:
            return None

        contextual_match = self._get_contextual_cached_message(query)
        if contextual_match:
            self._set_last_selected_email(str(contextual_match.get("id", "")))
            return contextual_match

        memory_rows = []
        valid_ids: List[str] = []
        for idx, email in enumerate(emails[:50], start=1):
            message_id = self._clean_text_value(str(email.get("id", "")))
            if not message_id:
                continue
            valid_ids.append(message_id)
            memory_rows.append(
                {
                    "index": idx,
                    "id": message_id,
                    "from": email.get("from", "Unknown"),
                    "subject": email.get("subject", "No Subject"),
                    "date": email.get("date", "Unknown"),
                    "snippet": str(email.get("snippet", ""))[:240],
                }
            )

        if not memory_rows:
            return None

        selection_input = (
            f"User request: {query}\n\n"
            "Recent Gmail memory (newest first):\n"
            f"{json.dumps(memory_rows, ensure_ascii=True)}\n\n"
            f"Valid message ids:\n{json.dumps(valid_ids, ensure_ascii=True)}\n\n"
            "Pick exactly one message_id from valid ids. "
            "If no email matches, return null message_id."
        )

        selection = await self.extract_parameters(
            user_message=selection_input,
            schema_description="""
- message_id: one value from the provided valid ids, or null if none matches
- reason: short explanation
            """,
            example_output='{"message_id": null, "reason": "brief reason"}',
            context=context,
        )

        selected_id = self._clean_text_value(str(selection.get("message_id", "")))
        if not selected_id or selected_id not in set(valid_ids):
            return None

        for email in emails:
            if str(email.get("id", "")) == selected_id:
                self._set_last_selected_email(selected_id)
                return email

        return None




