"""
Gmail Agent
Handles Gmail operations: send, draft, reply, summarize inbox
"""

import base64
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


class GmailAgent(BaseAgent):
    """Agent for Gmail operations."""

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
        "mark_as_read": "mark_read",
        "mark_email_as_read": "mark_read",
    }

    @classmethod
    def normalize_action(cls, action: str) -> str:
        cleaned = (action or "").strip().lower()
        return cls.ACTION_ALIASES.get(cleaned, cleaned)

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Determine action and execute Gmail operation."""
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
        try:
            list_response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages",
                params={"maxResults": 10, "labelIds": "INBOX"},
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Gmail", exc)

        if list_response.status_code != 200:
            return self.handle_google_api_error("Gmail", list_response)

        messages = list_response.json().get("messages", [])
        email_summaries = []

        for msg in messages[:5]:
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
                        "from": headers.get("From", "Unknown"),
                        "subject": headers.get("Subject", "No Subject"),
                        "date": headers.get("Date", "Unknown"),
                    }
                )

        summary_text = "\n".join(
            f"- From: {email_item['from'][:30]} | {email_item['subject'][:50]}"
            for email_item in email_summaries
        ) or "No recent emails found"

        return self.success(
            summary=f"Recent inbox ({len(email_summaries)} emails):\n{summary_text}",
            data={"emails": email_summaries},
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
        return self.success(
            summary=f"Found {len(email_summaries)} Gmail messages for '{query}':\n{summary_text}",
            data={"emails": email_summaries, "query": query},
        )

    async def read_email(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            message_match = await self._find_message_match(query=query or user_message)
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
        text_for_summary = body or snippet or "No readable body was available."

        summary = await self.llm_complete(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_message}\n\n"
                        f"From: {headers.get('From', 'Unknown')}\n"
                        f"Subject: {headers.get('Subject', 'No Subject')}\n"
                        f"Date: {headers.get('Date', 'Unknown')}\n\n"
                        f"Email body:\n{text_for_summary[:5000]}"
                    ),
                }
            ],
            system_prompt=(
                "You summarize Gmail messages for the user. Mention sender, topic, asks, deadlines, "
                "and any follow-up needed. Keep it concise and factual."
            ),
            context=context,
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

    async def mark_email_as_read(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            message_match = await self._find_message_match(query=query or user_message)
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
        try:
            list_response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages",
                params={"maxResults": max_results, "q": query or None, "labelIds": "INBOX" if not query else None},
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

        return {"emails": email_summaries}

    async def _find_message_match(self, query: str) -> Optional[Dict[str, Any]]:
        result = await self._fetch_message_metadata_list(max_results=5, query=query)
        if result.get("status") == "error":
            return result

        emails = result.get("emails", [])
        return emails[0] if emails else None

    async def _get_message_details(
        self,
        message_id: str,
        format_type: str = "full",
    ) -> Dict[str, Any]:
        try:
            response = await self.request_google_api(
                "GET",
                f"{GMAIL_BASE_URL}/messages/{message_id}",
                params={
                    "format": format_type,
                    "metadataHeaders": ["From", "Subject", "Date", "To"] if format_type == "metadata" else None,
                },
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




