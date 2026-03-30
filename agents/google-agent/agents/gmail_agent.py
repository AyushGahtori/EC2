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

    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Determine action and execute Gmail operation."""
        pending_task = self._get_pending_task(context)
        if pending_task and self._looks_like_follow_up_message(user_message, pending_task):
            action = pending_task.get("action", "send")
        else:
            action = await self._determine_action(user_message, context)

        logger.info(f"[gmail] action: {action}")

        if action == "send":
            return await self.send_email(user_message, context)
        if action == "draft":
            return await self.draft_email(user_message, context)
        if action == "summarize":
            return await self.summarize_inbox()
        if action == "reply":
            return await self.reply_email(user_message)
        if action == "list":
            return await self.list_emails()
        return await self.list_emails()

    async def _determine_action(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='action: one of "send", "draft", "summarize", "reply", "list"',
            example_output='{"action": "send"}',
            context=context,
        )
        return params.get("action", "list")

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

    async def summarize_inbox(self) -> Dict[str, Any]:
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

    async def list_emails(self) -> Dict[str, Any]:
        """List recent emails."""
        return await self.summarize_inbox()

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




