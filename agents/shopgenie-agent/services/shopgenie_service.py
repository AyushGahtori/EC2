from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover
    firebase_admin = None
    credentials = None
    firestore = None


@dataclass
class ShopGenieSettings:
    gemini_api_key: str = (os.getenv("GEMINI_API_KEY") or "").strip()
    gemini_model: str = (
        os.getenv("GEMINI_MODEL_FLASH")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GEMINI_MODEL_PRO")
        or "gemini-2.5-flash"
    ).strip()
    tavily_api_key: str = (os.getenv("TAVILY_API_KEY") or "").strip()
    youtube_api_key: str = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    google_client_id: str = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    google_client_secret: str = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


SETTINGS = ShopGenieSettings()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    cleaned = _clean(text).removeprefix("```json").removeprefix("```").rstrip("`").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _extract_json_list(text: str) -> list[dict[str, Any]]:
    cleaned = _clean(text).removeprefix("```json").removeprefix("```").rstrip("`").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except Exception:
        return []
    return []


async def _call_gemini(messages: list[dict[str, str]]) -> str | None:
    if not SETTINGS.gemini_api_key:
        return None

    merged_prompt = "\n\n".join(
        [f"{m.get('role', 'user').upper()}:\n{m.get('content', '')}" for m in messages]
    ).strip()
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{SETTINGS.gemini_model}:generateContent"
    )
    payload = {"contents": [{"role": "user", "parts": [{"text": merged_prompt}]}]}

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{endpoint}?key={SETTINGS.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
        texts = [str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)]
        merged = "\n".join([text for text in texts if text])
        return merged or None
    except Exception:
        return None


async def _search_tavily(query: str, budget: str | None = None) -> list[dict[str, Any]]:
    if not SETTINGS.tavily_api_key:
        return []

    searches = [
        f"best {query} buying guide comparison",
        f"top {query} features price review",
    ]
    if budget:
        searches.append(f"best {query} under {budget}")

    results: list[dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}
    endpoint = "https://api.tavily.com/search"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for search in searches[:3]:
            body = {
                "api_key": SETTINGS.tavily_api_key,
                "query": search,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": True,
            }
            try:
                resp = await client.post(endpoint, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json() or {}
                for item in data.get("results") or []:
                    if not isinstance(item, dict):
                        continue
                    results.append(
                        {
                            "title": _clean(item.get("title") or ""),
                            "content": _clean(item.get("content") or ""),
                            "url": _clean(item.get("url") or ""),
                        }
                    )
            except Exception:
                continue

    # Deduplicate by title+url
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in results:
        key = f"{row.get('title','')}|{row.get('url','')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:12]


def _fallback_products(query: str) -> list[dict[str, Any]]:
    base = query.title().strip() or "Requested Product"
    return [
        {
            "name": f"{base} - Top Pick",
            "price": "N/A",
            "why": "Selected as the top result based on available data.",
            "source": None,
        },
        {
            "name": f"{base} - Runner Up",
            "price": "N/A",
            "why": "Strong alternative with good overall value.",
            "source": None,
        },
    ]


def _extract_candidate_products_from_search(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        title = _clean(str(row.get("title") or ""))
        if not title:
            continue
        # Remove common suffixes often found in result titles
        normalized = re.split(r"\s+[\-|:]\s+", title)[0].strip()
        if len(normalized) < 3:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "name": normalized,
                "price": None,
                "why": "Identified from comparison sources.",
                "source": _clean(str(row.get("url") or "")) or None,
            }
        )
        if len(candidates) >= 5:
            break
    return candidates


async def _structure_products(query: str, budget: str | None, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return _fallback_products(query)

    raw_text = "\n\n".join(
        [f"Title: {r.get('title','')}\nURL: {r.get('url','')}\nSnippet: {r.get('content','')}" for r in rows[:10]]
    )
    system = (
        "Extract shopping products from search snippets. "
        "Return strict JSON array only, each item with keys: name, price, why, source."
    )
    user = (
        f"User query: {query}\nBudget: {budget or 'not specified'}\n\n"
        f"Search snippets:\n{raw_text}\n\n"
        "Return 3 to 5 distinct products."
    )
    raw = await _call_gemini(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )

    parsed = _extract_json_list(raw or "")
    products: list[dict[str, Any]] = []
    for item in parsed:
        name = _clean(str(item.get("name") or ""))
        if not name:
            continue
        products.append(
            {
                "name": name,
                "price": _clean(str(item.get("price") or "")) or None,
                "why": _clean(str(item.get("why") or "")) or "Matched the query constraints.",
                "source": _clean(str(item.get("source") or "")) or None,
            }
        )

    if products:
        return products[:5]

    extracted = _extract_candidate_products_from_search(rows)
    return extracted if extracted else _fallback_products(query)


async def _pick_best_product(
    query: str,
    budget: str | None,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    if not products:
        fallback = _fallback_products(query)[0]
        return {
            "bestProduct": fallback["name"],
            "why": fallback["why"],
            "reasoning": "Comparison data was limited, so this is the safest available top match.",
        }

    system = (
        "You are a product recommendation expert. "
        "Return strict JSON with keys: bestProduct, why, reasoning."
    )
    user = (
        f"Query: {query}\nBudget: {budget or 'not specified'}\n"
        f"Candidates JSON:\n{json.dumps(products, ensure_ascii=True)}\n"
        "Pick the single best product and justify clearly."
    )
    raw = await _call_gemini(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    parsed = _extract_json_obj(raw or "") or {}

    best_name = _clean(str(parsed.get("bestProduct") or ""))
    if not best_name:
        best_name = _clean(str(products[0].get("name") or "Top Pick"))

    why = _clean(str(parsed.get("why") or "")) or _clean(str(products[0].get("why") or "Selected based on available data."))
    reasoning = _clean(str(parsed.get("reasoning") or "")) or "Full comparison details were limited, so this is selected from the strongest matching evidence."

    return {
        "bestProduct": best_name,
        "why": why,
        "reasoning": reasoning,
    }


async def _find_youtube_review(product_name: str) -> str | None:
    if not product_name or not SETTINGS.youtube_api_key:
        return None

    endpoint = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": SETTINGS.youtube_api_key,
        "q": f"{product_name} review",
        "part": "snippet",
        "maxResults": 1,
        "type": "video",
        "relevanceLanguage": "en",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(endpoint, params=params, headers={"Referer": "https://localhost"})
            resp.raise_for_status()
            payload = resp.json() or {}
        items = payload.get("items") or []
        if not items:
            return None
        video_id = (((items[0] or {}).get("id") or {}).get("videoId"))
        if not video_id:
            return None
        return f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        return None


def _candidate_key_paths() -> list[str]:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        _clean(os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")),
        _clean(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
        "/home/ubuntu/app/.secrets/serviceAccountKey.json",
        os.path.join(repo_root, ".secrets", "serviceAccountKey.json"),
    ]
    return [path for path in candidates if path]


def _ensure_firebase() -> bool:
    if firebase_admin is None or credentials is None or firestore is None:
        return False
    if firebase_admin._apps:
        return True

    for path in _candidate_key_paths():
        if os.path.exists(path):
            firebase_admin.initialize_app(credentials.Certificate(path))
            return True

    try:
        firebase_admin.initialize_app()
        return True
    except Exception:
        return False


def _get_db():
    if not _ensure_firebase():
        return None
    return firestore.client()


def _get_google_provider_connection(user_id: str) -> dict[str, Any] | None:
    db = _get_db()
    if db is None or not user_id:
        return None
    snap = (
        db.collection("users")
        .document(user_id)
        .collection("providerConnections")
        .document("google")
        .get()
    )
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    access = _clean(str(data.get("accessToken") or ""))
    if not access:
        return None
    return data


def _is_token_expired(expires_at: Any) -> bool:
    if expires_at is None:
        return False

    try:
        if hasattr(expires_at, "timestamp"):
            exp_ts = float(expires_at.timestamp())
        elif isinstance(expires_at, (int, float)):
            exp_ts = float(expires_at)
        elif isinstance(expires_at, str) and expires_at.strip():
            exp_ts = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
        else:
            return False
        return exp_ts <= (time.time() + 45)
    except Exception:
        return False


async def _refresh_google_access_token(refresh_token: str) -> tuple[str | None, int | None]:
    if not refresh_token or not SETTINGS.google_client_id or not SETTINGS.google_client_secret:
        return None, None

    endpoint = "https://oauth2.googleapis.com/token"
    form = {
        "client_id": SETTINGS.google_client_id,
        "client_secret": SETTINGS.google_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(endpoint, data=form)
            resp.raise_for_status()
            payload = resp.json() or {}
        token = _clean(str(payload.get("access_token") or ""))
        expires_in = int(payload.get("expires_in") or 0)
        if not token:
            return None, None
        expires_at = int(time.time()) + max(0, expires_in)
        return token, expires_at
    except Exception:
        return None, None


def _persist_google_token(user_id: str, access_token: str, expires_at: int | None) -> None:
    db = _get_db()
    if db is None or not user_id or not access_token:
        return
    payload: dict[str, Any] = {
        "accessToken": access_token,
        "updatedAt": firestore.SERVER_TIMESTAMP if firestore else datetime.now(timezone.utc),
    }
    if expires_at:
        payload["expiresAt"] = expires_at
    (
        db.collection("users")
        .document(user_id)
        .collection("providerConnections")
        .document("google")
        .set(payload, merge=True)
    )


def _build_email_raw_message(to_email: str, subject: str, html_body: str) -> str:
    msg = MIMEMultipart("alternative")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


async def _send_gmail_message(access_token: str, raw_message: str) -> tuple[bool, str | None]:
    endpoint = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"raw": raw_message}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return True, None
            body = _clean(resp.text)
            return False, body or f"gmail_send_failed_{resp.status_code}"
    except Exception as exc:
        return False, str(exc)


async def send_email_if_requested(
    *,
    user_id: str | None,
    recipient_email: str | None,
    send_email: bool,
    query: str,
    best_product: str,
    why: str,
    reasoning: str,
    youtube_link: str | None,
) -> tuple[bool, str | None]:
    if not send_email:
        return False, "Email not requested."

    to_email = _clean(recipient_email)
    if not to_email or "@" not in to_email:
        return False, "Recipient email is missing or invalid."

    if not user_id:
        return False, "Missing user identity for Google email send."

    connection = _get_google_provider_connection(user_id)
    if not connection:
        return False, "Google connection not found. Connect Google Workspace Agent to enable email send."

    access_token = _clean(str(connection.get("accessToken") or ""))
    refresh_token = _clean(str(connection.get("refreshToken") or ""))
    expires_at = connection.get("expiresAt")

    if not access_token:
        return False, "Google access token is missing. Reconnect Google Workspace Agent."

    if _is_token_expired(expires_at):
        refreshed_token, refreshed_exp = await _refresh_google_access_token(refresh_token)
        if refreshed_token:
            access_token = refreshed_token
            _persist_google_token(user_id, refreshed_token, refreshed_exp)

    subject = f"ShopGenie Recommendation: {best_product}"
    youtube_html = (
        f"<p><a href=\"{youtube_link}\">Watch YouTube review</a></p>" if youtube_link else ""
    )
    body = (
        "<div style=\"font-family:Arial,sans-serif;max-width:600px;margin:0 auto;\">"
        f"<h2>ShopGenie Recommendation</h2><p><strong>Query:</strong> {query}</p>"
        f"<p><strong>Best Product:</strong> {best_product}</p>"
        f"<p><strong>Why:</strong> {why}</p>"
        f"<p><strong>Reasoning:</strong> {reasoning}</p>{youtube_html}</div>"
    )

    raw_msg = _build_email_raw_message(to_email, subject, body)
    ok, error = await _send_gmail_message(access_token, raw_msg)
    if ok:
        return True, "Email sent via connected Google account."

    if error and "401" in error and refresh_token:
        refreshed_token, refreshed_exp = await _refresh_google_access_token(refresh_token)
        if refreshed_token:
            _persist_google_token(user_id, refreshed_token, refreshed_exp)
            ok2, err2 = await _send_gmail_message(refreshed_token, raw_msg)
            if ok2:
                return True, "Email sent via connected Google account."
            return False, f"Email send failed after token refresh: {err2 or 'unknown error'}"

    return False, f"Email send failed: {error or 'unknown error'}"


async def run_shopgenie(
    *,
    query: str,
    user_id: str | None,
    budget: str | None,
    recipient_email: str | None,
    send_email: bool,
) -> dict[str, Any]:
    cleaned_query = _clean(query)
    if not cleaned_query:
        raise ValueError("query is required")

    search_rows = await _search_tavily(cleaned_query, budget)
    products = await _structure_products(cleaned_query, budget, search_rows)
    comparison = await _pick_best_product(cleaned_query, budget, products)

    best_product = _clean(str(comparison.get("bestProduct") or "")) or _clean(str(products[0].get("name") or "Top Pick"))
    why = _clean(str(comparison.get("why") or "")) or "Selected as the top result based on available data."
    reasoning = _clean(str(comparison.get("reasoning") or "")) or "Full comparison unavailable; this product was the top search result."
    youtube_link = await _find_youtube_review(best_product)

    email_sent, email_status = await send_email_if_requested(
        user_id=user_id,
        recipient_email=recipient_email,
        send_email=send_email,
        query=cleaned_query,
        best_product=best_product,
        why=why,
        reasoning=reasoning,
        youtube_link=youtube_link,
    )

    status = "success"
    if send_email and not email_sent:
        status = "partial_success"

    return {
        "status": status,
        "type": "shopgenie_result",
        "message": "ShopGenie recommendation ready.",
        "summary": f"Best product: {best_product}",
        "displayName": "ShopGenie Results",
        "result": {
            "query": cleaned_query,
            "bestProduct": best_product,
            "why": why,
            "reasoning": reasoning,
            "youtubeReview": youtube_link,
            "emailSent": email_sent,
            "emailStatus": email_status,
            "products": products,
        },
    }
