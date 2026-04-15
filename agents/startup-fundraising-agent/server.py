from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:  # pragma: no cover - handled gracefully at runtime
    firebase_admin = None
    credentials = None
    firestore = None


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AGENT_SLUG = "startup-fundraising-agent"
DISPLAY_NAME = "Fund Agent"
STATE_LOCK = threading.Lock()
MEMORY_STORE: dict[str, dict[str, Any]] = {}
FIRESTORE_CLIENT = None

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger(AGENT_SLUG)
UID_PATTERN = re.compile(r"^[A-Za-z0-9._:@-]{3,128}$")


EMBEDDED_INVESTORS: list[dict[str, Any]] = [
    {
        "name": "Northstar Seed Partners",
        "firm": "Northstar Ventures",
        "stage": ["pre-seed", "seed"],
        "industry": ["saas", "ai", "developer tools"],
        "geography": ["north america", "remote", "us"],
        "check_size": "$100k-$500k",
        "thesis": "Category-defining B2B software with clear distribution advantage.",
        "intro_angle": "Show how the product shortens sales cycles or removes a painful manual workflow.",
    },
    {
        "name": "Atlas Frontier",
        "firm": "Atlas Capital",
        "stage": ["seed", "series a"],
        "industry": ["fintech", "b2b", "infrastructure"],
        "geography": ["us", "new york", "san francisco"],
        "check_size": "$250k-$1m",
        "thesis": "Capital-efficient products with measurable revenue traction.",
        "intro_angle": "Lead with traction, retention, and the next wedge into a larger market.",
    },
    {
        "name": "Civic Growth",
        "firm": "Civic Ventures",
        "stage": ["pre-seed", "seed"],
        "industry": ["climate", "health", "marketplace"],
        "geography": ["us", "europe"],
        "check_size": "$150k-$750k",
        "thesis": "Founders building durable businesses with strong mission clarity.",
        "intro_angle": "Frame why this problem is urgent now and how the company can build trust fast.",
    },
    {
        "name": "Signal Peak",
        "firm": "Signal Peak Capital",
        "stage": ["seed", "series a"],
        "industry": ["ai", "data", "automation"],
        "geography": ["us", "canada"],
        "check_size": "$250k-$750k",
        "thesis": "Data-rich products with proprietary learning loops.",
        "intro_angle": "Show the learning loop, the moat, and the path to repeatable distribution.",
    },
    {
        "name": "Crestline Founders",
        "firm": "Crestline Ventures",
        "stage": ["pre-seed", "seed"],
        "industry": ["marketplace", "consumer", "commerce"],
        "geography": ["us", "los angeles", "new york"],
        "check_size": "$100k-$250k",
        "thesis": "Strong founder-market fit and early customer pull.",
        "intro_angle": "Lead with customer love, referral loops, or early waitlist growth.",
    },
    {
        "name": "Lattice B2B Fund",
        "firm": "Lattice Capital",
        "stage": ["seed", "series a"],
        "industry": ["saas", "security", "devtools"],
        "geography": ["us", "europe", "remote"],
        "check_size": "$200k-$800k",
        "thesis": "Technical products solving expensive enterprise pain points.",
        "intro_angle": "Share deployment velocity, integration depth, and buyer urgency.",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\n;/|]+", value)
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _normalize_slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean_text(value).lower()).strip("-")


def _parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = _clean_text(value).lower().replace(",", "").replace("$", "")
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith("k"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.endswith("bn"):
        multiplier = 1_000_000_000.0
        text = text[:-2]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _extract_keywords(payload: dict[str, Any]) -> list[str]:
    keywords = []
    for key in ("keywords", "focus_keywords", "tags", "thesis_keywords"):
        keywords.extend(_normalize_list(payload.get(key)))
    return [keyword.lower() for keyword in keywords]


def _get_firestore_client():
    global FIRESTORE_CLIENT
    if FIRESTORE_CLIENT is not None:
        return FIRESTORE_CLIENT
    if firebase_admin is None or credentials is None or firestore is None:
        return None

    key_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    try:
        if not firebase_admin._apps:
            if key_path and Path(key_path).exists():
                firebase_admin.initialize_app(credentials.Certificate(str(key_path)))
            else:
                firebase_admin.initialize_app()
        FIRESTORE_CLIENT = firestore.client()
        return FIRESTORE_CLIENT
    except Exception as exc:  # pragma: no cover - runtime dependent
        log.warning("Firestore unavailable, using in-memory fallback: %s", exc)
        return None


def _storage_mode() -> str:
    return "firestore" if _get_firestore_client() is not None else "memory"


def _resolve_uid(payload: dict[str, Any]) -> str:
    uid = _clean_text(payload.get("userId") or payload.get("user_id"))
    if not uid:
        raise ValueError("userId is required for user-isolated persistence.")
    if "/" in uid or not UID_PATTERN.match(uid):
        raise ValueError("Invalid userId format for Firestore-scoped storage.")
    return uid


def _get_user_store(uid: str) -> dict[str, Any]:
    with STATE_LOCK:
        return MEMORY_STORE.setdefault(
            uid,
            {
                "custom_investors": [],
                "conversations": [],
                "outreach_plans": [],
                "term_sheets": [],
                "agent_state": {},
            },
        )


def _agent_root(uid: str):
    db = _get_firestore_client()
    if db is None:
        return None
    return db.collection("users").document(uid).collection("agents").document(AGENT_SLUG)


def _agent_collection(uid: str, name: str):
    root = _agent_root(uid)
    if root is None:
        return None
    return root.collection(name)


def _merge_agent_state(uid: str, data: dict[str, Any]) -> None:
    data = dict(data)
    data["updatedAt"] = _now_iso()
    if _storage_mode() == "firestore":
        root = _agent_root(uid)
        if root is not None:
            root.set(data, merge=True)
    else:
        store = _get_user_store(uid)
        store["agent_state"].update(data)


def _load_custom_investors(uid: str) -> list[dict[str, Any]]:
    if _storage_mode() == "firestore":
        collection = _agent_collection(uid, "investors")
        if collection is None:
            return []
        results = []
        for doc in collection.stream():
            item = doc.to_dict() or {}
            item["id"] = doc.id
            results.append(item)
        return results
    return list(_get_user_store(uid)["custom_investors"])


def _save_custom_investor(uid: str, investor: dict[str, Any]) -> dict[str, Any]:
    record = dict(investor)
    record.setdefault("id", str(uuid.uuid4()))
    record["source"] = "custom"
    record["updatedAt"] = _now_iso()

    if _storage_mode() == "firestore":
        collection = _agent_collection(uid, "investors")
        if collection is not None:
            doc_id = str(record.get("id") or _normalize_slug(f"{record.get('name')}-{record.get('firm')}"))
            collection.document(doc_id).set(record, merge=True)
    else:
        store = _get_user_store(uid)
        key = f"{_normalize_slug(record.get('name'))}|{_normalize_slug(record.get('firm'))}"
        replaced = False
        for index, existing in enumerate(store["custom_investors"]):
            existing_key = f"{_normalize_slug(existing.get('name'))}|{_normalize_slug(existing.get('firm'))}"
            if existing_key == key:
                store["custom_investors"][index] = record
                replaced = True
                break
        if not replaced:
            store["custom_investors"].append(record)
    return record


def _append_conversation(uid: str, entry: dict[str, Any]) -> dict[str, Any]:
    record = dict(entry)
    record.setdefault("id", str(uuid.uuid4()))
    record["updatedAt"] = _now_iso()

    if _storage_mode() == "firestore":
        collection = _agent_collection(uid, "conversations")
        if collection is not None:
            collection.document(record["id"]).set(record, merge=True)
    else:
        _get_user_store(uid)["conversations"].append(record)
    return record


def _append_outreach(uid: str, entry: dict[str, Any]) -> dict[str, Any]:
    record = dict(entry)
    record.setdefault("id", str(uuid.uuid4()))
    record["updatedAt"] = _now_iso()

    if _storage_mode() == "firestore":
        collection = _agent_collection(uid, "outreach_plans")
        if collection is not None:
            collection.document(record["id"]).set(record, merge=True)
    else:
        _get_user_store(uid)["outreach_plans"].append(record)
    return record


def _append_term_sheet(uid: str, entry: dict[str, Any]) -> dict[str, Any]:
    record = dict(entry)
    record.setdefault("id", str(uuid.uuid4()))
    record["updatedAt"] = _now_iso()

    if _storage_mode() == "firestore":
        collection = _agent_collection(uid, "term_sheets")
        if collection is not None:
            collection.document(record["id"]).set(record, merge=True)
    else:
        _get_user_store(uid)["term_sheets"].append(record)
    return record


def _merged_investors(uid: str) -> list[dict[str, Any]]:
    investors = EMBEDDED_INVESTORS + _load_custom_investors(uid)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for investor in investors:
        key = f"{_normalize_slug(investor.get('name'))}|{_normalize_slug(investor.get('firm'))}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(investor)
    return merged


def _matches_any(text: str, tokens: list[str]) -> bool:
    haystack = text.lower()
    return any(token in haystack for token in tokens if token)


def _score_investor(payload: dict[str, Any], investor: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    stage = _clean_text(payload.get("stage")).lower()
    industry = _clean_text(payload.get("industry")).lower()
    geography = _clean_text(payload.get("geography")).lower()
    keywords = _extract_keywords(payload)
    raise_amount = _parse_money(payload.get("raise_amount") or payload.get("check_size"))
    investor_check = _parse_money(investor.get("check_size"))

    if stage and any(stage in option for option in investor.get("stage", [])):
        score += 32
        reasons.append(f"Stage fit for {stage}")
    if industry and any(industry in option for option in investor.get("industry", [])):
        score += 28
        reasons.append(f"Industry fit for {industry}")
    if geography and any(geography in option for option in investor.get("geography", [])):
        score += 12
        reasons.append(f"Geography fit for {geography}")
    if keywords and _matches_any(" ".join([investor.get("thesis", ""), investor.get("intro_angle", "")]), keywords):
        score += min(20, 5 * len(keywords))
        reasons.append("Keyword alignment with thesis")
    if raise_amount is not None and investor_check is not None:
        lower = investor_check * 0.5
        upper = investor_check * 2.5
        if lower <= raise_amount <= upper:
            score += 14
            reasons.append(f"Check size matches {investor.get('check_size')}")

    if investor.get("source", "embedded") == "custom":
        score += 6
        reasons.append("Saved in local pipeline")

    if not reasons:
        reasons.append("Broad thematic fit")
        score += 3

    return score, reasons


def _unique_conversation_id(value: str | None = None) -> str:
    return _clean_text(value) or str(uuid.uuid4())


def _determine_linkedin_dependency(payload: dict[str, Any]) -> dict[str, Any] | None:
    requested = any(
        bool(payload.get(key))
        for key in (
            "linkedin_required",
            "linkedin_connection_required",
            "request_linkedin_connection",
            "use_linkedin",
            "linkedin_agent_required",
        )
    )
    requested = requested or _clean_text(payload.get("preferred_channel")).lower() == "linkedin"
    connected = any(
        bool(payload.get(key))
        for key in (
            "linkedIn_agent_connected",
            "linkedin_agent_connected",
            "linkedin_connected",
            "is_linkedin_connected",
        )
    )
    if requested and not connected:
        return {
            "state": "needs_connection",
            "provider": "linkedIn agent",
            "message": "LinkedIn outreach is available once the LinkedIn agent connection is enabled.",
            "next_step": "Connect the linkedIn agent, then retry the outreach workflow.",
        }
    if requested and connected:
        return {
            "state": "connected",
            "provider": "linkedIn agent",
            "message": "LinkedIn connection is ready for warm outreach workflows.",
            "next_step": "Use LinkedIn as a secondary channel for personalized follow-up.",
        }
    return None


def _build_investor_search(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    uid = _resolve_uid(payload)
    investors = _merged_investors(uid)
    scored = []
    for investor in investors:
        score, reasons = _score_investor(payload, investor)
        item = dict(investor)
        item["match_score"] = score
        item["fit_reason"] = "; ".join(reasons)
        item["suggested_channel"] = "email" if _clean_text(payload.get("preferred_channel")).lower() != "linkedin" else "linkedin"
        scored.append(item)

    scored.sort(key=lambda row: (row["match_score"], row.get("updated_at", "")), reverse=True)
    limit = max(1, int(payload.get("limit") or 5))
    top_matches = scored[:limit]
    dependency = _determine_linkedin_dependency(payload)
    if dependency:
        status = "needs_linkedin_connection"
        result_type = "linkedin_dependency"
        message = dependency["message"]
    else:
        status = "success"
        result_type = "fund_investor_search"
        message = f"Found {len(top_matches)} investor matches for your fundraising brief."

    result = {
        "search_summary": {
            "user_id": uid,
            "filters": {
                "stage": payload.get("stage"),
                "industry": payload.get("industry"),
                "geography": payload.get("geography"),
                "raise_amount": payload.get("raise_amount"),
                "keywords": _extract_keywords(payload),
                "limit": limit,
            },
            "available_investors": len(investors),
            "matched_investors": len(top_matches),
        },
        "investors": top_matches,
        "next_steps": [
            "Shortlist the top 3 matches and personalize the opening line.",
            "Use the thesis and intro angle to tailor each email.",
            "Track replies and follow up within 3 to 5 business days.",
        ],
    }
    if dependency:
        result["linkedin_dependency"] = dependency
        result["next_steps"].insert(0, dependency["next_step"])
        _merge_agent_state(uid, {"lastAction": "search_investors", "lastSearch": result["search_summary"]})
        return status, result_type, message, result

    _merge_agent_state(uid, {"lastAction": "search_investors", "lastSearch": result["search_summary"]})
    return status, result_type, message, result


def _build_outreach_plan(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    uid = _resolve_uid(payload)
    startup = _clean_text(payload.get("startup_name") or payload.get("company_name") or "Your startup", "Your startup")
    stage = _clean_text(payload.get("stage"), "seed").lower()
    warm_intro = any(bool(payload.get(key)) for key in ("warm_intro", "warm_introduction", "has_warm_intro"))
    channel = _clean_text(payload.get("preferred_channel")).lower() or "email"
    dependency = _determine_linkedin_dependency(payload)
    investors = _merged_investors(uid)
    shortlist = []
    for investor in investors[:5]:
        score, reasons = _score_investor(payload, investor)
        shortlist.append(
            {
                "name": investor.get("name"),
                "firm": investor.get("firm"),
                "match_score": score,
                "fit_reason": "; ".join(reasons[:2]),
            }
        )

    sequence = [
        {
            "step": 1,
            "day": 0,
            "channel": channel if channel in ("email", "linkedin") else "email",
            "goal": "Open with relevance and traction",
            "copy_hint": f"Lead with why {startup} matters now and why this investor fits the stage.",
        },
        {
            "step": 2,
            "day": 3,
            "channel": "email",
            "goal": "Add a concise proof point",
            "copy_hint": "Share one metric, one customer signal, and one crisp market insight.",
        },
        {
            "step": 3,
            "day": 7,
            "channel": "linkedin" if dependency else "email",
            "goal": "Personal follow-up",
            "copy_hint": "Reference the prior note and keep the ask lightweight: a 15 minute intro call.",
        },
        {
            "step": 4,
            "day": 12,
            "channel": "email",
            "goal": "Answer likely objections",
            "copy_hint": "Address traction, market size, or why now in a short bullet list.",
        },
        {
            "step": 5,
            "day": 18,
            "channel": "email",
            "goal": "Final polite follow-up",
            "copy_hint": "Close the loop and make it easy to pass or book a call.",
        },
    ]

    if warm_intro:
        sequence[0]["goal"] = "Convert the warm intro immediately"
        sequence[0]["copy_hint"] = "Keep it short, ask for the intro, and make the reason to meet obvious."

    result = {
        "user_id": uid,
        "startup": startup,
        "stage": stage,
        "recommended_channel": channel,
        "shortlist": shortlist,
        "subject_lines": [
            f"{startup} is solving a real bottleneck in {stage} software",
            f"Quick intro: {startup} and a focused seed round",
            f"Short fundraising note from {startup}",
        ],
        "sequence": sequence,
        "message_framework": {
            "opening": "why you are reaching out",
            "proof": "one traction signal",
            "ask": "a short call or a warm introduction",
            "close": "a simple yes, no, or who should I speak with",
        },
    }
    if dependency:
        result["linkedin_dependency"] = dependency
        _append_outreach(uid, result)
        return ("needs_linkedin_connection", "fund_outreach_plan", dependency["message"], result)

    _append_outreach(uid, result)
    return ("success", "fund_outreach_plan", "Outreach plan ready.", result)


def _build_conversation_tracking(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    uid = _resolve_uid(payload)
    investor_name = _clean_text(payload.get("investor_name"), "Unknown investor")
    investor_firm = _clean_text(payload.get("investor_firm"))
    status = _clean_text(payload.get("status"), "contacted").lower()
    notes = _clean_text(payload.get("notes"))
    conversation_id = _unique_conversation_id(payload.get("conversation_id"))
    follow_up_date = _clean_text(payload.get("follow_up_date"))
    last_message = _clean_text(payload.get("last_message"))

    if investor_name != "Unknown investor":
        _save_custom_investor(
            uid,
            {
                "name": investor_name,
                "firm": investor_firm,
                "stage": _clean_text(payload.get("stage")),
                "industry": _clean_text(payload.get("industry")),
                "geography": _clean_text(payload.get("geography")),
                "linkedin_url": _clean_text(payload.get("linkedin_url")),
                "email": _clean_text(payload.get("investor_email")),
                "notes": notes,
            }
        )

    log_entry = _append_conversation(
        uid,
        {
            "conversation_id": conversation_id,
            "investor_name": investor_name,
            "investor_firm": investor_firm,
            "status": status,
            "follow_up_date": follow_up_date or None,
            "last_message": last_message or None,
            "notes": notes or None,
        }
    )

    next_step_map = {
        "interested": "Send the deck, define next meeting, and confirm decision makers.",
        "replied": "Reply with a short answer and suggest a call while momentum is high.",
        "meeting": "Prepare a tight agenda, investor questions, and a clear close.",
        "closed": "Record the outcome and either celebrate or archive the lead.",
        "contacted": "Wait 3 to 5 business days, then follow up with a sharper hook.",
    }
    next_step = next_step_map.get(status, "Keep the relationship moving with a clean next ask.")

    result = {
        "user_id": uid,
        "conversation_id": conversation_id,
        "tracking": log_entry,
        "pipeline_status": status,
        "next_step": next_step,
        "follow_up_date": follow_up_date or None,
    }
    _merge_agent_state(uid, {"lastAction": "track_conversation", "lastConversationId": conversation_id})
    return "success", "fund_conversation_tracking", "Conversation tracked successfully.", result


def _build_term_sheet_guidance(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    uid = _resolve_uid(payload)
    instrument = _clean_text(payload.get("instrument_type") or payload.get("round_type"), "SAFE")
    valuation_cap = _clean_text(payload.get("valuation_cap") or payload.get("valuation"))
    raise_amount = _clean_text(payload.get("raise_amount") or payload.get("investment_amount"))
    discount = _clean_text(payload.get("discount"), "N/A")
    pro_rata = bool(payload.get("pro_rata"))
    investor_name = _clean_text(payload.get("investor_name"), "the investor")

    commercial_terms = [
        f"Confirm the instrument: {instrument}.",
        f"Set the raise size and valuation guardrails before legal drafting. Raise amount: {raise_amount or 'N/A'}.",
        "Be explicit about dilution, conversion triggers, and investor rights.",
    ]
    if valuation_cap:
        commercial_terms.append(f"Valuation cap: {valuation_cap}.")
    if discount and discount != "N/A":
        commercial_terms.append(f"Discount: {discount}.")
    if pro_rata:
        commercial_terms.append("Include pro rata language only if it fits the round structure.")

    watchouts = [
        "Do not let ambiguity creep into maturity, conversion, or pro rata language.",
        "Keep any side letters aligned with the main document.",
        "Get counsel to review economics before you send anything for signature.",
    ]
    negotiation_points = [
        f"Ask {investor_name} to confirm expectations on board involvement and information rights.",
        "Protect the company from uncapped MFN or open-ended most-favored provisions.",
        "If the round is early, keep the document simple and execution-friendly.",
    ]
    checklist = [
        "Match the document to the actual round stage.",
        "Check names, entity details, and signature blocks.",
        "Confirm wire instructions only through a trusted channel.",
        "Have legal review the final draft before circulation.",
    ]

    result = {
        "user_id": uid,
        "instrument_type": instrument,
        "summary": f"Term sheet guidance prepared for {instrument}.",
        "commercial_terms": commercial_terms,
        "negotiation_points": negotiation_points,
        "founder_watchouts": watchouts,
        "closing_checklist": checklist,
        "legal_note": "This is commercial guidance, not legal advice.",
    }
    _append_term_sheet(uid, result)
    _merge_agent_state(uid, {"lastAction": "term_sheet_guidance"})
    return "success", "fund_term_sheet_guidance", "Term sheet guidance ready.", result


def _gemini_summary(kind: str, payload: dict[str, Any], result: dict[str, Any]) -> str | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    model = (
        os.getenv("GEMINI_MODEL", "").strip()
        or os.getenv("GEMINI_MODEL_PRO", "").strip()
        or os.getenv("GEMINI_MODEL_FLASH", "").strip()
        or "gemini-2.5-pro"
    )
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = (
        f"You are Fund Agent. Write a concise, founder-friendly summary for {kind}. "
        f"Keep it practical, avoid legal advice, and mention LinkedIn dependency only if present.\n\n"
        f"Payload:\n{payload}\n\nResult:\n{result}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 220},
    }
    try:
        response = httpx.post(endpoint, params={"key": api_key}, json=body, timeout=20.0)
        response.raise_for_status()
        data = response.json()
        for candidate in data.get("candidates", []):
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            texts = [part.get("text") for part in parts if isinstance(part, dict) and part.get("text")]
            if texts:
                return "".join(texts).strip()
    except Exception as exc:  # pragma: no cover - runtime dependent
        log.info("Gemini summary fallback used: %s", exc)
    return None


def _build_fundraising_plan(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    uid = _resolve_uid(payload)
    _, _, _, investor_result = _build_investor_search(payload)
    _, _, _, outreach_result = _build_outreach_plan(payload)
    _, _, _, term_result = _build_term_sheet_guidance(payload)
    dependency = investor_result.get("linkedin_dependency") or outreach_result.get("linkedin_dependency")

    result = {
        "user_id": uid,
        "startup": _clean_text(payload.get("startup_name") or payload.get("company_name"), "Your startup"),
        "fundraising_goal": _clean_text(payload.get("raise_amount") or payload.get("fundraising_goal"), "N/A"),
        "investor_search": investor_result,
        "outreach_plan": outreach_result,
        "conversation_tracking_template": {
            "statuses": ["contacted", "replied", "interested", "meeting", "closed"],
            "recommended_follow_up": "3 to 5 business days after the last note unless the investor asked for more time.",
        },
        "term_sheet_guidance": term_result,
        "recommended_next_actions": [
            "Shortlist the top matches.",
            "Send the opening note with a clear ask.",
            "Log every reply so the pipeline stays current.",
            "Prepare term sheet language only after mutual interest is confirmed.",
        ],
    }
    if dependency:
        result["linkedin_dependency"] = dependency
    ai_summary = _gemini_summary("fundraising_plan", payload, result)
    if ai_summary:
        result["ai_summary"] = ai_summary

    status = "needs_linkedin_connection" if dependency else "success"
    message = "Fundraising plan ready." if not dependency else dependency["message"]
    _merge_agent_state(uid, {"lastAction": "generate_fundraising_plan", "lastSummary": result.get("ai_summary") or result["recommended_next_actions"][0]})
    return status, "fundraising_plan", message, result


class FundAgentActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str = "generate_fundraising_plan"
    startup_name: str | None = None
    company_name: str | None = None
    industry: str | None = None
    stage: str | None = None
    geography: str | None = None
    raise_amount: str | None = None
    check_size: str | None = None
    keywords: list[str] | str | None = None
    limit: int | None = 5
    preferred_channel: str | None = None
    warm_intro: bool | None = None
    linkedIn_agent_connected: bool | None = None
    linkedin_agent_connected: bool | None = None
    linkedin_connected: bool | None = None
    request_linkedin_connection: bool | None = None
    linkedin_required: bool | None = None
    linkedin_connection_required: bool | None = None
    investor_name: str | None = None
    investor_firm: str | None = None
    investor_email: str | None = None
    status: str | None = None
    notes: str | None = None
    conversation_id: str | None = None
    follow_up_date: str | None = None
    last_message: str | None = None
    instrument_type: str | None = None
    round_type: str | None = None
    valuation_cap: str | None = None
    valuation: str | None = None
    discount: str | None = None
    pro_rata: bool | None = None
    model_config = ConfigDict(extra="allow")


class FundAgentActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    displayName: str | None = None
    error: str | None = None
    action: str | None = None


FundAgentActionRequest.model_rebuild()
FundAgentActionResponse.model_rebuild()


app = FastAPI(title="Startup Fundraising Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _execute_action(payload: dict[str, Any]) -> FundAgentActionResponse:
    action = _clean_text(payload.get("action") or "generate_fundraising_plan").strip().lower().replace("-", "_")
    _resolve_uid(payload)
    dependency = _determine_linkedin_dependency(payload)
    handlers = {
        "search_investors": _build_investor_search,
        "plan_outreach": _build_outreach_plan,
        "track_conversation": _build_conversation_tracking,
        "term_sheet_guidance": _build_term_sheet_guidance,
        "generate_fundraising_plan": _build_fundraising_plan,
        "fundraising_plan": _build_fundraising_plan,
        "linkedin_dependency_check": lambda data: (
            "needs_linkedin_connection"
            if dependency and dependency["state"] == "needs_connection"
            else "success",
            "linkedin_dependency",
            dependency["message"] if dependency else "LinkedIn dependency is satisfied.",
            {"linkedin_dependency": dependency or {"state": "connected", "provider": "linkedIn agent", "message": "LinkedIn dependency is satisfied.", "next_step": "Proceed with outreach."}},
        ),
    }
    handler = handlers.get(action)
    if not handler:
        return FundAgentActionResponse(
            status="failed",
            type="fund_agent_error",
            message=f"Unsupported action: {payload.get('action')}.",
            result={
                "supported_actions": [
                    "search_investors",
                    "plan_outreach",
                    "track_conversation",
                    "term_sheet_guidance",
                    "generate_fundraising_plan",
                    "linkedin_dependency_check",
                ]
            },
            displayName=DISPLAY_NAME,
            action=payload.get("action"),
        )

    status, result_type, message, result = handler(payload)
    return FundAgentActionResponse(
        status=status,
        type=result_type,
        message=message,
        result=result,
        displayName=DISPLAY_NAME,
        action=payload.get("action"),
    )


@app.post("/startup-fundraising-agent/action", response_model=FundAgentActionResponse)
@app.post("/fund-agent/action", response_model=FundAgentActionResponse)
@app.post("/fundraising/action", response_model=FundAgentActionResponse)
async def execute_fund_agent_action(req: FundAgentActionRequest) -> FundAgentActionResponse:
    try:
        payload = req.model_dump(mode="python", exclude_none=False)
        return _execute_action(payload)
    except ValueError as exc:
        return FundAgentActionResponse(
            status="failed",
            type="fund_agent_error",
            message="Fund Agent requires authenticated user context before it can save or read plan state.",
            displayName=DISPLAY_NAME,
            error=str(exc),
            result={
                "needs_input": {
                    "field": "userId",
                    "reason": "required_for_user_scoped_firestore_access",
                }
            },
        )
    except Exception:
        log.exception("Fund Agent action failed")
        return FundAgentActionResponse(
            status="failed",
            type="fund_agent_error",
            message="The fundraising workflow could not complete right now. Please try again.",
            displayName=DISPLAY_NAME,
        )


@app.get("/health")
@app.get("/startup-fundraising-agent/health")
@app.get("/fundraising/health")
async def health() -> dict[str, Any]:
    storage_mode = _storage_mode()
    uid = "default"
    if storage_mode == "firestore":
        investor_count = len(_load_custom_investors(uid))
        conversation_count = 0
        conversations = _agent_collection(uid, "conversations")
        if conversations is not None:
            try:
                conversation_count = len(list(conversations.limit(100).stream()))
            except Exception:
                conversation_count = 0
    else:
        store = _get_user_store(uid)
        investor_count = len(store["custom_investors"])
        conversation_count = len(store["conversations"])
    return {
        "status": "healthy" if storage_mode == "firestore" or storage_mode == "memory" else "degraded",
        "agent": AGENT_SLUG,
        "displayName": DISPLAY_NAME,
        "version": "1.0.0",
        "storage": storage_mode,
        "summary": {
            "custom_investors": investor_count,
            "conversation_logs": conversation_count,
            "persistence_paths": {
                "state": f"users/{{uid}}/agents/{AGENT_SLUG}",
                "investors": f"users/{{uid}}/agents/{AGENT_SLUG}/investors",
                "conversations": f"users/{{uid}}/agents/{AGENT_SLUG}/conversations",
                "outreach": f"users/{{uid}}/agents/{AGENT_SLUG}/outreach_plans",
                "termSheets": f"users/{{uid}}/agents/{AGENT_SLUG}/term_sheets",
            },
        },
        "gemini": {
            "enabled": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "model": (
                os.getenv("GEMINI_MODEL", "").strip()
                or os.getenv("GEMINI_MODEL_PRO", "").strip()
                or os.getenv("GEMINI_MODEL_FLASH", "").strip()
                or "gemini-2.5-pro"
            ),
        },
    }
