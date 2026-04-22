"""
Base Agent Class
All agents inherit from this, providing common functionality
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

import httpx

import os

AUTH_REQUIRED_MESSAGE = "This action requires Google authorization. Please connect your Google account."

class GoogleAuthRequiredError(Exception):
    pass

logger = logging.getLogger(__name__)

GEMINI_MODEL_MAP = {
    "gemini-3-pro": os.getenv("GEMINI_MODEL_PRO", "gemini-2.5-pro"),
    "gemini-3-flash": os.getenv("GEMINI_MODEL_FLASH", "gemini-2.5-flash"),
    "gemini-3.1-flash-lite": os.getenv("GEMINI_MODEL_FLASH_LITE", "gemini-2.5-flash-lite"),
    "gemini-3-flash-preview": os.getenv("GEMINI_MODEL_FLASH", "gemini-2.5-flash"),
    "gemini-3.1-pro-preview": os.getenv("GEMINI_MODEL_PRO", "gemini-2.5-pro"),
    "gemini-3.1-flash-lite-preview": os.getenv("GEMINI_MODEL_FLASH_LITE", "gemini-2.5-flash-lite"),
}


class BaseAgent(ABC):
    """
    Abstract base class for all agents.
    Provides common LLM extraction, error handling, and logging.
    """

    def __init__(self, access_token: str, user_id: str, refresh_token: str = ""):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = user_id
        self.agent_name = self.__class__.__name__

    def _resolve_ollama_model(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Resolve model with cloud/local hybrid routing and safe fallbacks."""
        context = context or {}

        direct_model = context.get("ollama_model") or context.get("model")
        if isinstance(direct_model, str) and direct_model.strip():
            return direct_model.strip()

        mode = context.get("ollama_mode") or context.get("model_mode") or context.get("model_source")
        if not mode and isinstance(context.get("use_cloud"), bool):
            mode = "cloud" if context.get("use_cloud") else "local"

        cloud_model = os.getenv("OLLAMA_MODEL_CLOUD", "").strip()
        local_model = os.getenv("OLLAMA_MODEL_LOCAL", "").strip()
        default_model = os.getenv("OLLAMA_DEFAULT_MODEL", "").strip()
        legacy_model = os.getenv("OLLAMA_MODEL", "").strip()

        if isinstance(mode, str):
            selected_mode = mode.strip().lower()
            if selected_mode == "cloud" and cloud_model:
                return cloud_model
            if selected_mode == "local" and local_model:
                return local_model

        if default_model:
            return default_model
        if legacy_model:
            return legacy_model
        if cloud_model:
            return cloud_model
        if local_model:
            return local_model
        return "qwen2.5:7b"

    def _resolve_gemini_model(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Resolve Gemini model with aliases and env defaults."""
        context = context or {}
        direct_model = context.get("model") or context.get("gemini_model")
        if isinstance(direct_model, str) and direct_model.strip():
            model_key = direct_model.strip()
            return GEMINI_MODEL_MAP.get(model_key, model_key)

        fallback = os.getenv("GEMINI_MODEL", "").strip()
        if fallback:
            return GEMINI_MODEL_MAP.get(fallback, fallback)

        return GEMINI_MODEL_MAP.get("gemini-3-flash", "gemini-2.5-flash")

    def _should_use_gemini(self, context: Optional[Dict[str, Any]] = None) -> bool:
        """Decide whether Gemini should be used for LLM calls."""
        context = context or {}
        if not os.getenv("GEMINI_API_KEY", "").strip():
            return False

        provider_hint = (
            str(context.get("llm_provider", "")).strip().lower()
            or os.getenv("AGENT_LLM_PROVIDER", "").strip().lower()
        )
        if provider_hint == "gemini":
            return True
        if provider_hint == "ollama":
            return False

        model_hint = str(context.get("model", "")).strip().lower()
        if model_hint.startswith("gemini"):
            return True

        default_model = os.getenv("GEMINI_MODEL", "").strip().lower()
        if default_model.startswith("gemini"):
            return True

        return False

    async def _call_gemini(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        force_json: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Call Gemini generateContent endpoint and return plain text."""
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        gemini_model = self._resolve_gemini_model(context)
        contents = []

        for msg in messages:
            role = "model" if msg.get("role") in {"assistant", "model"} else "user"
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            contents.append({"role": role, "parts": [{"text": text}]})

        if not contents:
            contents.append({"role": "user", "parts": [{"text": "Hello"}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }

        if system_prompt.strip():
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

        if force_json:
            payload["generationConfig"]["response_mime_type"] = "application/json"

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{gemini_model}:generateContent?key={api_key}"
        )

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return ""

        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(
            part.get("text", "") for part in parts if isinstance(part.get("text"), str)
        ).strip()

    @abstractmethod
    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a user request. Must be implemented by all agents.

        Returns:
            {status, summary, data, agent}
        """

    async def extract_parameters(
        self,
        user_message: str,
        schema_description: str,
        example_output: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Use LLM to extract structured parameters from natural language.
        """
        prompt_sections = ["Extract the following parameters from the user's message."]

        if context:
            pending_task = context.get("pending_task")
            if pending_task:
                prompt_sections.append(
                    "Pending task context:\n"
                    + json.dumps(pending_task, indent=2, ensure_ascii=True, default=str)
                )

            memory_summary = context.get("memory_summary")
            if memory_summary:
                prompt_sections.append(f"Chat memory summary:\n{memory_summary}")

            history = context.get("conversation_history", [])[-8:]
            if history:
                formatted_history = "\n".join(
                    f"- {msg.get('role', 'unknown')}: {msg.get('content', '')[:400]}"
                    for msg in history
                )
                prompt_sections.append(f"Recent conversation history:\n{formatted_history}")

            agent_outputs = context.get("agent_outputs") or pending_task.get("agent_outputs") if pending_task else context.get("agent_outputs")
            if agent_outputs:
                prompt_sections.append(
                    "Agent observations:\n"
                    + json.dumps(agent_outputs, indent=2, ensure_ascii=True, default=str)
                )

        prompt_sections.append(f'User message: "{user_message}"')
        prompt_sections.append(
            f"""Extract: {schema_description}

Return ONLY valid JSON in this format:
{example_output}

If a value is not mentioned, use null.
If the current message is continuing a pending request, merge it with the pending context."""
        )

        prompt = "\n\n".join(prompt_sections)

        try:
            content = ""
            if self._should_use_gemini(context):
                content = await self._call_gemini(
                    [{"role": "user", "content": prompt}],
                    force_json=True,
                    context=context,
                )
            else:
                ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
                ollama_model = self._resolve_ollama_model(context)
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(
                        f"{ollama_url}/api/chat",
                        json={
                            "model": ollama_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "stream": False,
                            "format": "json"
                        }
                    )
                    if res.status_code == 200:
                        content = res.json().get("message", {}).get("content", "{}")

            if not content:
                return {}

            try:
                return json.loads(content)
            except json.JSONDecodeError:
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(content[start : end + 1])
        except Exception as e:
            logger.error(f"Failed to parse LLM json: {e}")

        return {}

    async def llm_complete(self, messages: list, system_prompt: str = "", context: Optional[Dict[str, Any]] = None) -> str:
        """Generate plain text using Gemini (preferred) or Ollama fallback."""
        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        try:
            if self._should_use_gemini(context):
                gemini_messages = [
                    msg for msg in formatted_messages if msg.get("role") != "system"
                ]
                gemini_system_prompt = system_prompt or ""
                return await self._call_gemini(
                    gemini_messages,
                    system_prompt=gemini_system_prompt,
                    context=context,
                )

            ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
            ollama_model = self._resolve_ollama_model(context)
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(
                    f"{ollama_url}/api/chat",
                    json={
                        "model": ollama_model,
                        "messages": formatted_messages,
                        "stream": False
                    }
                )
                if res.status_code == 200:
                    return res.json().get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"Failed to generate LLM text completion: {e}")

        return "I could not summarize the results due to an internal error."

    def success(self, summary: str, data: Any = None) -> Dict[str, Any]:
        """Return a success response."""
        logger.info("? Success [%s] %s", self.agent_name, summary)
        return {
            "status": "success",
            "agent": self.agent_name,
            "summary": summary,
            "data": data,
        }

    def failure(
        self,
        error: str,
        data: Any = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a failure response."""
        summary = message or error
        logger.error("[%s] Error: %s", self.agent_name, summary)
        return {
            "status": "error",
            "agent": self.agent_name,
            "summary": summary,
            "error": error,
            "message": summary,
            "data": data,
        }

    def auth_required(self, data: Any = None) -> Dict[str, Any]:
        """Return a structured auth-required response."""
        logger.error("?? Auth Error [%s] %s", self.agent_name, AUTH_REQUIRED_MESSAGE)
        return self.failure(
            error="AUTH_REQUIRED",
            message=AUTH_REQUIRED_MESSAGE,
            data=data,
        )

    async def refresh_google_access_token(self) -> bool:
        """Refresh the Google access token and persist it if possible."""
        from google_client import GoogleAuthRequired, refresh_access_token

        try:
            self.access_token = refresh_access_token(self.refresh_token or None)
            return True
        except GoogleAuthRequired as exc:
            self.auth_url = exc.auth_url
            return False

    async def request_google_api(
        self,
        method: str,
        url: str,
        retry_on_failure: bool = False,
        **kwargs,
    ) -> httpx.Response:
        """Send a Google API request with optional safe retry."""
        from google_client import acquire_google_token, GoogleAuthRequired
        
        try:
            self.access_token = acquire_google_token(
                access_token=self.access_token or None,
                refresh_token=self.refresh_token or None,
            )
        except GoogleAuthRequired as exc:
            self.auth_url = exc.auth_url
            logger.warning("🔒 Auth Error [%s] Google sign-in required", self.agent_name)
            raise GoogleAuthRequiredError()

        extra_headers = kwargs.pop("headers", {})
        max_attempts = 2 if retry_on_failure else 1

        for attempt in range(1, max_attempts + 1):
            headers = {**extra_headers, "Authorization": f"Bearer {self.access_token}"}

            logger.info("🌐 Google API Call [%s] %s (attempt %s)", method.upper(), url, attempt)

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.request(method, url, headers=headers, **kwargs)
            except httpx.RequestError:
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(0.4)
                continue

            if response.status_code == 401:
                logger.warning("🔒 Auth Error [%s] %s returned 401", method.upper(), url)
                # Token expired — try to refresh
                from google_client import refresh_access_token
                try:
                    self.access_token = refresh_access_token(self.refresh_token or None)
                    continue  # retry with new token
                except GoogleAuthRequired as exc:
                    self.auth_url = exc.auth_url
                    raise GoogleAuthRequiredError()

            if retry_on_failure and response.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts:
                await asyncio.sleep(0.4)
                continue

            return response

        raise RuntimeError(f"{self.agent_name} could not complete Google API request")

    def format_google_api_error(self, service_name: str, response: httpx.Response) -> str:
        """Create a readable Google API error message from a response."""
        detail = f"{service_name} API error: {response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            error_data = payload.get("error")
            if isinstance(error_data, dict):
                message = error_data.get("message") or error_data.get("status")
                if message:
                    detail += f" - {message}"
            elif isinstance(error_data, str):
                detail += f" - {error_data}"
        else:
            raw_text = response.text.strip()
            if raw_text:
                detail += f" - {raw_text[:300]}"

        if response.status_code == 401:
            detail += ". Please reconnect your Google account if this keeps happening."

        return detail

    def handle_google_exception(
        self,
        service_name: str,
        exc: Exception,
        data: Any = None,
    ) -> Dict[str, Any]:
        """Map Google request exceptions to structured agent responses."""
        if isinstance(exc, GoogleAuthRequiredError):
            auth_url = getattr(self, "auth_url", None)
            if auth_url:
                return {
                    "status": "action_required",
                    "agent": self.agent_name,
                    "summary": f"Please connect your Google account first: {auth_url}",
                    "auth_url": auth_url,
                }
            return self.auth_required(data=data)
        return self.failure(f"{service_name} request failed: {exc}", data=data)

    def handle_google_api_error(
        self,
        service_name: str,
        response: httpx.Response,
        data: Any = None,
    ) -> Dict[str, Any]:
        """Map Google API responses to structured errors."""
        if response.status_code == 401:
            return self.auth_required(data=data)
        return self.failure(self.format_google_api_error(service_name, response), data=data)

    def get_auth_headers(self) -> Dict[str, str]:
        """Get Google API authorization headers."""
        return {"Authorization": f"Bearer {self.access_token}"}

