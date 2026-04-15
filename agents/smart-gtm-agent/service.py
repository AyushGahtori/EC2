from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

try:
    from tavily import TavilyClient
except Exception:  # pragma: no cover
    TavilyClient = None  # type: ignore[assignment]

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover
    firebase_admin = None  # type: ignore[assignment]
    credentials = firestore = None  # type: ignore[assignment]

from schemas import (
    SmartGTMActionRequest,
    SmartGTMActionResponse,
    SmartGTMReport,
    SmartGTMSection,
    SmartGTMSource,
)

logger = logging.getLogger(__name__)
UID_PATTERN = re.compile(r"^[A-Za-z0-9._:@-]{3,128}$")

AGENT_SLUG = "smart-gtm-agent"
DISPLAY_NAME = os.getenv("SMART_GTM_DISPLAY_NAME", "Smart GTM Agent")
MODE_LABELS = {"research": "Research", "gtm": "Go-to-Market", "channel": "Channel"}
MODE_ALIASES = {
    "research": "research",
    "research_company": "research",
    "company_research": "research",
    "gtm": "gtm",
    "go-to-market": "gtm",
    "go_to_market": "gtm",
    "go to market": "gtm",
    "channel": "channel",
    "distribution": "channel",
    "partner": "channel",
}

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"
TAVILY_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or os.getenv("GEMINI_MODEL_FLASH") or "gemini-2.5-flash").strip()
FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "").strip()
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip() or None
CACHE_COLLECTION = "agentReports"
CACHE_DOC = AGENT_SLUG
REPORT_CACHE_COLLECTION = "reports"

_TAVILY = TavilyClient(api_key=TAVILY_API_KEY) if TavilyClient and TAVILY_API_KEY else None
_FIRESTORE = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def validated_uid(raw_uid: str | None) -> str | None:
    if raw_uid is None:
        return None
    uid = raw_uid.strip()
    if not uid:
        return None
    if "/" in uid or not UID_PATTERN.match(uid):
        raise ValueError("Invalid userId format for Firestore-scoped storage.")
    return uid


def is_url(value: str) -> bool:
    value = value.strip()
    return bool(re.match(r"^(https?://|www\.)", value, re.I) or re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/|$)", value))


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        return value
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def extract_company_name(raw: str | None) -> str:
    if not raw:
        return "Unknown Company"
    value = raw.strip()
    if not value:
        return "Unknown Company"
    if is_url(value):
        parsed = urlparse(normalize_url(value) or value)
        host = (parsed.netloc or parsed.path).lower().removeprefix("www.")
        parts = [p for p in re.split(r"[.\-_/]+", host) if p and p not in {"com", "io", "co", "ai", "net", "org", "app"}]
        if parts:
            return normalize_text(" ".join(parts)).title()
    return normalize_text(value).title()


def mode_from_action(action: str | None, requested_mode: str | None) -> str | None:
    for candidate in (requested_mode, action):
        if not candidate:
            continue
        normalized = candidate.strip().lower().replace(" ", "_")
        if normalized in MODE_ALIASES:
            return MODE_ALIASES[normalized]
    return None


def resolve_target(req: SmartGTMActionRequest) -> tuple[str, str | None, str]:
    raw = (req.companyUrl or req.url or req.query or req.companyName or "").strip()
    if not raw:
        raise ValueError("companyUrl or query is required")
    company_url = normalize_url(raw) if is_url(raw) else None
    company_name = (req.companyName or "").strip() or extract_company_name(company_url or raw)
    target_key = company_url or normalize_text(company_name).lower()
    return target_key, company_url, company_name


def _firestore_db():
    global _FIRESTORE
    if _FIRESTORE is not None:
        return _FIRESTORE
    if firebase_admin is None or credentials is None or firestore is None or not FIREBASE_SERVICE_ACCOUNT_KEY:
        return None
    if not firebase_admin._apps:
        key_path = Path(FIREBASE_SERVICE_ACCOUNT_KEY)
        if not key_path.is_absolute():
            key_path = Path.cwd() / key_path
        if not key_path.exists():
            return None
        cred = credentials.Certificate(str(key_path))
        kwargs: dict[str, Any] = {}
        if FIREBASE_PROJECT_ID:
            kwargs["projectId"] = FIREBASE_PROJECT_ID
        app = firebase_admin.initialize_app(cred, options=kwargs or None, name=f"{AGENT_SLUG}-admin")
    else:
        app = firebase_admin.get_app()
    _FIRESTORE = firestore.client(app=app)
    return _FIRESTORE


def _cache_doc(uid: str, target_key: str, mode: str):
    db = _firestore_db()
    if db is None:
        return None
    key = hashlib.sha256(f"{target_key}|{mode}".encode("utf-8")).hexdigest()
    return db.collection("users").document(uid).collection(CACHE_COLLECTION).document(CACHE_DOC).collection(REPORT_CACHE_COLLECTION).document(key)


def load_cached_report(uid: str, target_key: str, mode: str) -> dict[str, Any] | None:
    ref = _cache_doc(uid, target_key, mode)
    if ref is None:
        return None
    snap = ref.get()
    return snap.to_dict() if snap.exists else None


def save_cached_report(uid: str, target_key: str, mode: str, payload: dict[str, Any]) -> None:
    ref = _cache_doc(uid, target_key, mode)
    if ref is None:
        return
    ref.set({"uid": uid, "agentId": AGENT_SLUG, "mode": mode, "companyName": payload.get("companyName"), "companyUrl": payload.get("companyUrl"), "cachedAt": now_iso(), "payload": payload, "updatedAt": now_iso()})


def build_queries(company_name: str, mode: str) -> list[str]:
    if mode == "research":
        return [f"{company_name} company overview competitors", f"{company_name} funding customers"]
    if mode == "gtm":
        return [f"{company_name} target market pricing channels", f"{company_name} go to market strategy"]
    return [f"{company_name} distribution partners channels", f"{company_name} reseller ecosystem"]


def flatten_search(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in results:
        title = normalize_text(str(item.get("title", "")))
        content = normalize_text(str(item.get("content", "")))
        url = normalize_text(str(item.get("url", "")))
        if title or content:
            items.append({"title": title or "Search result", "content": content, "url": url})
    return items


def collect_web_data(company_url: str | None, company_name: str, mode: str) -> tuple[str | None, list[dict[str, str]], dict[str, str]]:
    status = {"extract": "skipped", "search": "skipped"}
    extract_text = None
    search_items: list[dict[str, str]] = []
    if _TAVILY and company_url:
        try:
            extract_resp = _TAVILY.extract(urls=company_url, extract_depth="basic", format="markdown")
            extract_results = extract_resp.get("results") or []
            if extract_results:
                first = extract_results[0] if isinstance(extract_results, list) else extract_results
                extract_text = normalize_text(str(first.get("raw_content") or first.get("content") or "")) if isinstance(first, dict) else normalize_text(str(first))
                status["extract"] = "ok" if extract_text else "empty"
            else:
                status["extract"] = "empty"
        except Exception as exc:  # pragma: no cover
            logger.exception("Smart GTM extract failed")
            status["extract"] = f"error:{exc.__class__.__name__}"
        try:
            combined: list[dict[str, Any]] = []
            for query in build_queries(company_name, mode):
                resp = _TAVILY.search(query=query, search_depth=TAVILY_SEARCH_DEPTH, max_results=TAVILY_MAX_RESULTS)
                combined.extend(resp.get("results") or [])
            search_items = flatten_search(combined)
            status["search"] = "ok" if search_items else "empty"
        except Exception as exc:  # pragma: no cover
            logger.exception("Smart GTM search failed")
            status["search"] = f"error:{exc.__class__.__name__}"
    else:
        if not _TAVILY:
            status["search"] = "disabled:TAVILY_API_KEY"
        if not company_url:
            status["extract"] = "disabled:no-url"
    return extract_text, search_items, status


def split_sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", normalize_text(text)) if part]


def pick_signals(text: str, limit: int = 5) -> list[str]:
    signals: list[str] = []
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if len(sentence) < 24:
            continue
        if any(word in lowered for word in ("platform", "saas", "software", "service", "team", "customer", "partner", "pricing", "channel")):
            signals.append(sentence[:180].strip())
        if len(signals) >= limit:
            break
    return signals


def infer_category(corpus: str) -> str:
    corpus = corpus.lower()
    pairs = [
        ("AI / machine learning", ["ai", "machine learning", "llm", "agent", "copilot"]),
        ("B2B SaaS", ["saas", "subscription", "software", "platform"]),
        ("Developer tools", ["developer", "api", "sdk", "devops"]),
        ("Marketing / growth software", ["marketing", "growth", "campaign", "analytics", "gtm"]),
        ("E-commerce / retail", ["ecommerce", "retail", "shop", "commerce"]),
        ("Fintech", ["fintech", "payments", "billing", "bank"]),
        ("Services / consultancy", ["consulting", "agency", "services", "advisory"]),
    ]
    for label, tokens in pairs:
        if any(token in corpus for token in tokens):
            return label
    return "Software / internet business"


def infer_icp(corpus: str, category: str) -> list[str]:
    lower = corpus.lower()
    items = [
        "Founders and small teams" if "founder" in lower or "startup" in lower else "",
        "Growth-stage SaaS teams" if "saas" in lower or "software" in lower else "",
        "Enterprise operators" if any(token in lower for token in ("enterprise", "security", "compliance")) else "",
        "Marketing and revenue teams" if any(token in lower for token in ("marketing", "revenue", "pipeline")) else "",
        "Developers and technical buyers" if any(token in lower for token in ("developer", "api", "sdk", "integration")) else "",
    ]
    return [item for item in items if item][:4] or [f"Buyers aligned with {category.lower()}"]


def infer_channels(mode: str, corpus: str) -> list[str]:
    lower = corpus.lower()
    base = {
        "research": ["SEO pages for use cases, comparisons, and problem-aware queries.", "Founder-led thought leadership and category education.", "Direct outreach to buyers, analysts, and reviewers."],
        "gtm": ["Comparison pages, pricing pages, and product-led demo paths.", "Outbound to the best-fit ICP with a tight proof-point pitch.", "Partner motion for adjacent ecosystems and integrations."],
        "channel": ["Reseller and referral partners with the same buyer access.", "Co-marketing with adjacent platforms and niche communities.", "Marketplaces, app ecosystems, and affiliates where relevant."],
    }[mode]
    if "developer" in lower or "api" in lower:
        base.append("Developer docs, launch posts, and community forums.")
    if "enterprise" in lower:
        base.append("Systems integrators and solution partners.")
    return base[:4]


def infer_risks(mode: str, corpus: str) -> list[str]:
    risks = ["Public data may be incomplete, so validate assumptions before spending heavily.", "Competitor pages can overstate differentiation; check current product proof points."]
    if "enterprise" in corpus.lower():
        risks.append("Longer sales cycles may slow payback.")
    if mode == "channel":
        risks.append("Partner enablement and incentives need clear ownership.")
    return risks[:3]


def section(title: str, summary: str, bullets: list[str], evidence: list[str] | None = None) -> SmartGTMSection:
    return SmartGTMSection(title=title, summary=normalize_text(summary), bullets=[normalize_text(b) for b in bullets if normalize_text(b)][:6], evidence=[normalize_text(e) for e in (evidence or []) if normalize_text(e)][:4])


def sources_from(extract_text: str | None, search_items: list[dict[str, str]]) -> list[SmartGTMSource]:
    items: list[SmartGTMSource] = []
    if extract_text:
        items.append(SmartGTMSource(kind="extract", title="Tavily extract", excerpt=extract_text[:280]))
    for item in search_items[:5]:
        items.append(SmartGTMSource(kind="search", title=item["title"], url=item["url"] or None, excerpt=item["content"][:220] if item["content"] else None))
    return items


def build_research(company_name: str, company_url: str | None, corpus: str, extract_text: str | None, search_items: list[dict[str, str]]) -> SmartGTMReport:
    category = infer_category(corpus)
    signals = pick_signals(corpus)
    competitors = [item["title"] for item in search_items[:5] if item["title"]]
    sections = [
        section("Overview", f"{company_name} appears to operate in {category}.", [f"Inferred category: {category}.", f"Sampled from {len(search_items)} search result(s).", "Validate homepage, product pages, and pricing directly."] + ([f"Signal: {signals[0]}"] if signals else []), [extract_text[:240]] if extract_text else []),
        section("Competitors", "Search results surfaced likely competitors and adjacent alternatives.", [f"Candidate competitor: {name}" for name in competitors[:4]] or ["No direct competitor titles surfaced in the sample."], [item["url"] for item in search_items[:4] if item["url"]]),
        section("Market Observations", "The company footprint suggests positioning themes worth validating.", signals or ["No rich extract was available, so the report leans on URL and search cues.", "Use product pages, reviews, and pricing pages to confirm the motion."]),
    ]
    key_takeaways = [f"{company_name} is most likely a {category} business.", "Validate the live positioning before making strategic assumptions.", "Use competitor pages to verify pricing, packaging, and proof points."]
    risks = infer_risks("research", corpus)
    next_steps = ["Review the homepage, pricing, and case study pages for exact messaging.", "Cross-check competitor claims against current product pages.", "Turn the findings into a tighter competitive brief."]
    report = SmartGTMReport(companyName=company_name, companyUrl=company_url, mode="research", modeLabel=MODE_LABELS["research"], generatedAt=now_iso(), sourceStatus={}, companySignals=signals, competitorSignals=competitors, keyTakeaways=key_takeaways, risks=risks, nextSteps=next_steps, sources=sources_from(extract_text, search_items), sections=sections, reportMarkdown="")
    report.reportMarkdown = build_markdown(report)
    return report


def build_gtm(company_name: str, company_url: str | None, corpus: str, extract_text: str | None, search_items: list[dict[str, str]]) -> SmartGTMReport:
    category = infer_category(corpus)
    icp = infer_icp(corpus, category)
    signals = pick_signals(corpus)
    competitors = [item["title"] for item in search_items[:5] if item["title"]]
    sections = [
        section("Target Market", f"The strongest GTM wedge appears to be {category.lower()} buyers.", [f"Primary market: {category}.", f"Likely buyer groups: {', '.join(icp[:3])}.", "Confirm the highest-conviction segment with live customer evidence."], [extract_text[:240]] if extract_text else []),
        section("Ideal Customer Profile", "The ICP should map to the pain points signaled in the footprint.", [f"ICP: {item}" for item in icp], [item["url"] for item in search_items[:4] if item["url"]]),
        section("Messaging & Positioning", "Messaging should translate the product into a measurable business outcome.", ["Lead with outcome, speed, and reduced manual work.", "Make the category comparison explicit and easy to understand.", "Use proof points, numbers, and a short demo path."] + ([f"Observed signal: {signals[0]}"] if signals else [])),
        section("Channels", "A mixed motion usually works best while the category is still being validated.", infer_channels("gtm", corpus)),
        section("Metrics & KPIs", "These metrics keep the GTM plan measurable and decision-oriented.", ["Website conversion rate.", "Demo-to-close conversion rate.", "Qualified pipeline from each channel.", "Cost per qualified opportunity.", "Time to first value for trial users."]),
    ]
    key_takeaways = [f"{company_name} should focus on {category.lower()} buyers first.", "A simple, evidence-led message is safer than broad category claims.", "Distribution should blend owned content, outbound, and partner-led reach."]
    risks = infer_risks("gtm", corpus)
    next_steps = ["Turn the strongest buyer segment into a one-page ICP brief.", "Translate the positioning into homepage and sales copy.", "Prioritize the first two acquisition channels and define funnel KPIs."]
    report = SmartGTMReport(companyName=company_name, companyUrl=company_url, mode="gtm", modeLabel=MODE_LABELS["gtm"], generatedAt=now_iso(), sourceStatus={}, companySignals=signals, competitorSignals=competitors, keyTakeaways=key_takeaways, risks=risks, nextSteps=next_steps, sources=sources_from(extract_text, search_items), sections=sections, reportMarkdown="")
    report.reportMarkdown = build_markdown(report)
    return report


def build_channel(company_name: str, company_url: str | None, corpus: str, extract_text: str | None, search_items: list[dict[str, str]]) -> SmartGTMReport:
    signals = pick_signals(corpus)
    competitors = [item["title"] for item in search_items[:5] if item["title"]]
    sections = [
        section("Primary Channels", "The primary path should match buyer urgency and product complexity.", infer_channels("channel", corpus), [extract_text[:240]] if extract_text else []),
        section("Digital Channels", "Digital channels should capture demand and educate the market.", ["SEO for use cases, comparisons, and category education.", "Paid search on high-intent problem-aware terms.", "Content-led social distribution for credibility and reach."]),
        section("Partnerships & Alliances", "Partnerships work best when the partner already touches the same buyer.", ["Target adjacent platforms, agencies, and implementation partners.", "Build a lightweight referral motion before a heavier reseller model.", "Offer a simple co-marketing or co-sell package."]),
        section("Risks & Dependencies", "Channel programs fail when ownership, incentives, or enablement are vague.", infer_risks("channel", corpus)),
    ]
    key_takeaways = ["Channel choice should follow buyer trust and purchase complexity.", "The highest-leverage partners are usually adjacent, not generic.", "A small number of channels executed well beats a broad launch."]
    risks = infer_risks("channel", corpus)
    next_steps = ["Rank the top 3 distribution paths by speed, trust, and cost.", "Create partner criteria and a lightweight enablement kit.", "Define the metrics that will decide whether a channel scales."]
    report = SmartGTMReport(companyName=company_name, companyUrl=company_url, mode="channel", modeLabel=MODE_LABELS["channel"], generatedAt=now_iso(), sourceStatus={}, companySignals=signals, competitorSignals=competitors, keyTakeaways=key_takeaways, risks=risks, nextSteps=next_steps, sources=sources_from(extract_text, search_items), sections=sections, reportMarkdown="")
    report.reportMarkdown = build_markdown(report)
    return report


def build_markdown(report: SmartGTMReport) -> str:
    parts = [f"# {report.modeLabel} Report: {report.companyName}", f"- Company URL: {report.companyUrl or 'Not provided'}", f"- Generated: {report.generatedAt}"]
    if report.companySignals:
        parts.append("## Company Signals")
        parts.extend(f"- {item}" for item in report.companySignals[:5])
    if report.competitorSignals:
        parts.append("## Competitor Signals")
        parts.extend(f"- {item}" for item in report.competitorSignals[:5])
    for sec in report.sections:
        parts.append(f"## {sec.title}")
        parts.append(sec.summary)
        parts.extend(f"- {item}" for item in sec.bullets)
        if sec.evidence:
            parts.append("Evidence:")
            parts.extend(f"- {item}" for item in sec.evidence)
    if report.keyTakeaways:
        parts.append("## Key Takeaways")
        parts.extend(f"- {item}" for item in report.keyTakeaways)
    if report.risks:
        parts.append("## Risks & Gaps")
        parts.extend(f"- {item}" for item in report.risks)
    if report.nextSteps:
        parts.append("## Next Steps")
        parts.extend(f"- {item}" for item in report.nextSteps)
    if report.sources:
        parts.append("## Sources")
        parts.extend(f"- {src.kind}: {src.title}{f' ({src.url})' if src.url else ''}" for src in report.sources)
    return "\n\n".join(parts).strip()


def gemini_rewrite(report: SmartGTMReport) -> str | None:
    if not GEMINI_API_KEY:
        return None
    prompt = f"Rewrite this report as polished markdown without inventing facts.\nCompany: {report.companyName}\nMode: {report.modeLabel}\nSource status: {json.dumps(report.sourceStatus, ensure_ascii=False)}\nCurrent draft:\n{report.reportMarkdown}\nKeep sections concise, muted, and business-ready."
    try:
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent", params={"key": GEMINI_API_KEY}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates") or []
        if candidates:
            parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
            text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
            if text:
                return text
    except Exception:  # pragma: no cover
        logger.exception("Gemini rewrite failed")
    return None


def build_response(report: SmartGTMReport, cached: bool = False, cached_at: str | None = None) -> SmartGTMActionResponse:
    report.cached = cached
    report.cachedAt = cached_at
    message = f"{report.modeLabel} report ready for {report.companyName}."
    if cached:
        message = f"Loaded cached {report.modeLabel.lower()} report for {report.companyName}."
    return SmartGTMActionResponse(status="success", type="smart_gtm_result", message=message, summary=report.keyTakeaways[0] if report.keyTakeaways else message, result=report, displayName=DISPLAY_NAME)


def execute_smart_gtm(req: SmartGTMActionRequest) -> SmartGTMActionResponse:
    mode = mode_from_action(req.action, req.mode)
    if mode not in MODE_LABELS:
        return SmartGTMActionResponse(status="failed", type="smart_gtm_result", error=f"Unsupported action: {req.action}", message="Smart GTM Agent supports research, go-to-market, and channel modes.", displayName=DISPLAY_NAME)
    try:
        target_key, company_url, company_name = resolve_target(req)
    except ValueError as exc:
        return SmartGTMActionResponse(status="failed", type="smart_gtm_result", error=str(exc), message="Please provide a company URL or company query.", displayName=DISPLAY_NAME)
    try:
        uid = validated_uid(req.userId)
    except ValueError as exc:
        return SmartGTMActionResponse(status="failed", type="smart_gtm_result", error=str(exc), message="Smart GTM requires a valid authenticated userId to use saved reports.", displayName=DISPLAY_NAME)
    if uid and not req.forceFresh:
        cached = load_cached_report(uid, target_key, mode)
        if cached and isinstance(cached.get("payload"), dict):
            cached_response = SmartGTMActionResponse.model_validate(cached["payload"])
            if cached_response.result:
                return build_response(cached_response.result, cached=True, cached_at=cached.get("cachedAt"))
    extract_text, search_items, source_status = collect_web_data(company_url, company_name, mode)
    corpus = " ".join(filter(None, [extract_text or "", " ".join(f"{item['title']} {item['content']}" for item in search_items)]))
    report = build_research(company_name, company_url, corpus, extract_text, search_items) if mode == "research" else build_gtm(company_name, company_url, corpus, extract_text, search_items) if mode == "gtm" else build_channel(company_name, company_url, corpus, extract_text, search_items)
    report.sourceStatus = {**source_status, "cache": "miss" if uid else "disabled:no-user"}
    rewritten = gemini_rewrite(report)
    if rewritten:
        report.reportMarkdown = rewritten
    response = build_response(report)
    if uid:
        save_cached_report(uid, target_key, mode, response.model_dump(mode="json"))
    return response
