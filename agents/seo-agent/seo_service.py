from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    import trafilatura
except Exception:  # pragma: no cover - optional dependency
    trafilatura = None

from schemas import (
    SEOActionRequest,
    SEOActionResponse,
    SEOAnalysisResult,
    SEOArticleAudit,
    SEOContentBrief,
    SEOReportSection,
    SEOSearchInsights,
    SEOSectionEdits,
)

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "are", "as", "at", "be", "because",
    "been", "before", "being", "between", "both", "but", "by", "can", "could", "did", "do",
    "does", "doing", "for", "from", "had", "has", "have", "having", "he", "her", "here",
    "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "like", "may", "me", "more", "most", "my", "no", "not", "of", "on", "one", "or",
    "other", "our", "out", "over", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those", "through",
    "to", "too", "under", "up", "use", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "will", "with", "would", "you", "your",
}

PLACEHOLDER_TOKENS = {
    "paste", "optional", "insert", "provide", "add", "replace", "here",
    "title", "content", "article", "draft", "url", "notes", "text",
}

WEAK_QUERY_TOKENS = {
    "best", "paste", "optional", "insert", "provide", "add", "replace", "here",
    "title", "content", "article", "draft", "url", "notes", "text",
}


@dataclass
class GeminiSettings:
    api_key: str = (os.getenv("GEMINI_API_KEY") or "").strip()
    model: str = (
        os.getenv("GEMINI_MODEL_FLASH")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GEMINI_MODEL_PRO")
        or "gemini-3-flash-preview"
    ).strip()
    fallback_models: tuple[str, ...] = ()


SETTINGS = GeminiSettings()
SETTINGS.fallback_models = tuple(
    model
    for model in dict.fromkeys(
        [
            SETTINGS.model,
            (os.getenv("GEMINI_MODEL_FLASH") or "").strip(),
            (os.getenv("GEMINI_MODEL") or "").strip(),
            (os.getenv("GEMINI_MODEL_PRO") or "").strip(),
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
            "gemini-3.1-flash-lite-preview",
        ]
    )
    if model
)


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']+", text.lower())


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        cleaned = _clean(item)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def _significant_terms(text: str, *, drop_weak: bool = False) -> list[str]:
    keep_short = {"ai", "crm", "seo", "saas", "b2b", "b2c", "api", "ui", "ux"}
    terms: list[str] = []
    for token in _tokens(text):
        if token in STOPWORDS:
            continue
        if drop_weak and token in WEAK_QUERY_TOKENS:
            continue
        if len(token) <= 2 and token not in keep_short:
            continue
        terms.append(token)
    return terms


def _looks_like_placeholder(value: str | None) -> bool:
    cleaned = _clean(value).lower()
    if not cleaned:
        return False
    if re.search(r"[\[\(<].{0,40}\b(paste|optional|insert|provide|add)\b.{0,40}[\]\)>]", cleaned):
        return True
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    if tokens and len(tokens) <= 8:
        placeholder_count = sum(token in PLACEHOLDER_TOKENS for token in tokens)
        if placeholder_count >= max(2, len(tokens) - 1):
            return True
    return cleaned in {
        "paste title", "paste article", "paste draft", "paste url",
        "optional title", "optional draft", "optional draft or notes",
        "title", "content", "article", "draft", "url", "notes",
    }


def _sanitize_optional_text(value: str | None) -> str:
    cleaned = _clean(value)
    if not cleaned or _looks_like_placeholder(cleaned):
        return ""
    return cleaned


def _sanitize_url(value: str | None) -> str | None:
    cleaned = _sanitize_optional_text(value)
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return cleaned


def _has_meaningful_article_text(text: str) -> bool:
    cleaned = _sanitize_optional_text(text)
    if not cleaned:
        return False
    return _word_count(cleaned) >= 12 or len(cleaned) >= 80


def _compress_topic(text: str) -> str:
    terms = _significant_terms(text, drop_weak=True)
    return " ".join(terms[:6]).strip()


def _ngram_phrases(text: str, limit: int = 6) -> list[str]:
    terms = _significant_terms(text, drop_weak=True)
    if len(terms) < 2:
        return []
    out: list[str] = []
    max_n = min(4, len(terms))
    for n in range(max_n, 1, -1):
        for idx in range(len(terms) - n + 1):
            phrase = " ".join(terms[idx : idx + n])
            if phrase not in out:
                out.append(phrase)
            if len(out) >= limit:
                return out
    return out


def _phrases(text: str, limit: int = 8) -> list[str]:
    words = _significant_terms(text, drop_weak=True)
    if not words:
        return []
    out: list[str] = []
    for token, _count in Counter(words).most_common(limit * 2):
        if token not in out:
            out.append(token)
        if len(out) >= limit:
            break
    return out


def _word_count(text: str) -> int:
    return len(_tokens(text))


def _extract_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", _clean(text)) if s.strip()]


def _fallback_questions(seed: str, limit: int = 6) -> list[str]:
    query = _clean(seed).rstrip("?")
    lower = query.lower()
    base_topic = _compress_topic(query) or query
    questions: list[str] = []

    if lower.startswith("how to "):
        subject = query[7:].strip() or base_topic
        questions.extend(
            [
                f"How do you {subject}?",
                f"Why does {subject} matter?",
                f"What mistakes should you avoid when trying to {subject}?",
                f"What are the best ways to {subject}?",
            ]
        )
    elif lower.startswith("best "):
        subject = query[5:].strip() or base_topic
        questions.extend(
            [
                f"What should you look for in {subject}?",
                f"Which {subject} options are best for different use cases?",
                f"How do you compare {subject} fairly?",
                f"What features matter most when choosing {subject}?",
            ]
        )
    elif lower.startswith("what is "):
        subject = query[8:].strip() or base_topic
        questions.extend(
            [
                f"What is {subject}?",
                f"Why does {subject} matter?",
                f"How does {subject} work in practice?",
                f"What are the best practices for {subject}?",
            ]
        )
    else:
        questions.extend(
            [
                f"What is {base_topic}?",
                f"Why does {base_topic} matter?",
                f"How do you improve {base_topic}?",
                f"What mistakes should you avoid with {base_topic}?",
            ]
        )

    return _dedupe(questions)[:limit]


def _extract_questions(text: str, seed: str, limit: int = 6) -> list[str]:
    qs = [s for s in _extract_sentences(text) if s.endswith("?")]
    if qs:
        return qs[:limit]
    return _fallback_questions(seed, limit=limit)


def _infer_topic(request: SEOActionRequest) -> str:
    for candidate in (request.topic, request.query, request.title):
        cleaned = _sanitize_optional_text(candidate)
        if cleaned:
            return cleaned
    content = _sanitize_optional_text(request.content)
    if content:
        phrases = _phrases(content, 4)
        if phrases:
            return " ".join(phrases[:3]).strip()
    return "seo content optimization"


def _build_query(topic: str, title: str | None, content: str | None) -> str:
    title_clean = _sanitize_optional_text(title)
    if title_clean:
        return title_clean
    if topic:
        return topic.strip()
    content_clean = _sanitize_optional_text(content)
    if content_clean:
        phrases = _phrases(content_clean, 4)
        if phrases:
            return " ".join(phrases[:3])
    return "content optimization"


def _normalize_domain(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    return host or "unknown-source"


def _strip_html(raw: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean(text)


def _fetch_url_text(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(timeout=float(os.getenv("SEO_AGENT_URL_TIMEOUT", "30")), follow_redirects=True, headers=headers) as client:
            html = client.get(url).text
        if trafilatura is not None:
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=False,
                include_images=False,
            )
            if extracted and len(extracted.strip()) > 100:
                return _clean(extracted)
        return _strip_html(html)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Could not extract %s: %s", url, exc)
        return ""


def _serpapi_search(query: str, engine: str) -> dict[str, Any]:
    api_key = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not api_key:
        return {}
    try:
        resp = httpx.get(
            "https://serpapi.com/search.json",
            params={"engine": engine, "q": query, "api_key": api_key},
            timeout=float(os.getenv("SERPAPI_TIMEOUT_SECONDS", "25")),
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:  # pragma: no cover - external dependency
        logger.warning("SerpApi search failed (%s / %s): %s", engine, query, exc)
        return {}


def _fallback_serp(query: str, article_text: str | None = None) -> dict[str, Any]:
    base_topic = _compress_topic(" ".join(filter(None, [query, article_text or ""]))) or _clean(query)
    phrase_variants = _ngram_phrases(" ".join(filter(None, [query, article_text or ""])), 4)
    related = _dedupe(
        phrase_variants
        + [f"{base_topic} examples", f"{base_topic} best practices", f"{base_topic} checklist"]
    )
    return {
        "organic_results": [],
        "related_questions": [{"question": item} for item in _fallback_questions(query, limit=6)],
        "related_searches": [{"query": item} for item in related[:8]],
        "ai_overview": {"answer": f"Readers want practical guidance on {base_topic}."},
    }


def _collect_serp(query: str, article_text: str | None = None) -> SEOSearchInsights:
    ai_mode = _serpapi_search(query, "google_ai_mode")
    google = _serpapi_search(query, "google")
    if not ai_mode and not google:
        ai_mode = _fallback_serp(query, article_text)
        google = ai_mode

    organic = (ai_mode.get("organic_results") or google.get("organic_results") or [])[:8]
    top_titles: list[str] = []
    top_domains: list[str] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        title = _clean(str(item.get("title") or ""))
        link = _clean(str(item.get("link") or item.get("url") or ""))
        if title:
            top_titles.append(title)
        if link:
            domain = _normalize_domain(link)
            if domain not in top_domains:
                top_domains.append(domain)

    questions = []
    for item in (ai_mode.get("related_questions") or google.get("related_questions") or [])[:10]:
        text = _clean(str(item.get("question") if isinstance(item, dict) else item))
        if text:
            questions.append(text)
    if not questions:
        questions = _extract_questions(article_text or query, query)
    questions = _dedupe(questions)

    related = []
    for item in (ai_mode.get("related_searches") or google.get("related_searches") or [])[:10]:
        text = _clean(str(item.get("query") if isinstance(item, dict) else item))
        if text:
            related.append(text)
    if not related:
        base_topic = _compress_topic(" ".join(filter(None, [query, article_text or ""]))) or query
        related = _dedupe(
            _ngram_phrases(" ".join(filter(None, [query, article_text or ""])), 5)
            + [f"{base_topic} examples", f"{base_topic} best practices", f"{base_topic} guide"]
        )
    related = _dedupe(related)

    overview = ai_mode.get("ai_overview") or google.get("ai_overview") or {}
    if isinstance(overview, dict):
        overview_summary = _clean(str(overview.get("answer") or overview.get("summary") or overview.get("text") or ""))
    else:
        overview_summary = _clean(str(overview))
    if not overview_summary:
        overview_summary = f"Searchers want practical, trustworthy guidance around {query}."

    intent = "Informational - readers want a focused SEO brief or optimization plan."
    lower = f"{query} {article_text or ''}".lower()
    if lower.startswith("how to "):
        intent = "Informational - readers want step-by-step guidance they can apply immediately."
    if any(token in lower for token in ("price", "pricing", "cost", "best", "compare", "tool", "software")):
        intent = "Commercial investigation - readers are comparing options and looking for decision support."
    if any(token in lower for token in ("optimize", "audit", "rewrite", "improve")):
        intent = "Transactional - readers want direct optimization guidance for existing content."

    competitor = "Top results usually win by answering the main query quickly and backing claims with clear structure and examples."
    if intent.startswith("Commercial investigation"):
        competitor = "Top results usually win with comparison tables, feature breakdowns, pricing context, trade-offs, and use-case recommendations."
    elif intent.startswith("Transactional"):
        competitor = "Top results usually win with audit checklists, step-by-step fixes, before-and-after examples, and clear implementation guidance."
    elif lower.startswith("how to "):
        competitor = "Top results usually win with practical step-by-step instructions, examples, and a concise checklist readers can apply quickly."
    if top_domains:
        competitor += f" Common domains include {', '.join(top_domains[:4])}."

    primary = _dedupe(
        [query]
        + [item.replace("-", " ") for item in _ngram_phrases(query, 3)]
        + [item.replace("-", " ") for item in _phrases(query, 4)]
    )
    if article_text:
        for phrase in _phrases(article_text, 4):
            normalized = phrase.replace("-", " ")
            if normalized not in primary:
                primary.append(normalized)

    return SEOSearchInsights(
        primaryKeywords=primary[:5],
        relatedKeywords=related[:10],
        relatedQuestions=questions[:10],
        searchIntent=intent,
        competitorAnalysis=competitor,
        aiOverviewSummary=overview_summary,
        topResultTitles=top_titles,
        topDomains=top_domains,
    )


def _fallback_brief(query: str, insights: SEOSearchInsights) -> SEOContentBrief:
    topic = query.strip().title()
    lower = query.lower().strip()
    if lower.startswith("how to "):
        subject = query[7:].strip() or query
        outline = [
            f"1. Quick answer: how to {subject}",
            f"2. Why {subject} matters",
            f"3. Step-by-step approach to {subject}",
            "4. Common mistakes and how to avoid them",
            "5. Examples, checklist, and FAQ",
            "6. Conclusion and next-step CTA",
        ]
        headings = [
            f"How to {subject.title()}",
            f"Why {subject.title()} Matters",
            f"Step-by-Step Plan to {subject.title()}",
            "Common Mistakes to Avoid",
            "Frequently Asked Questions",
        ]
    elif lower.startswith("best "):
        subject = query[5:].strip() or query
        outline = [
            f"1. What makes a great {subject}",
            f"2. Best {subject} options by use case",
            "3. Features, pricing, and trade-offs",
            "4. Comparison framework and buying criteria",
            "5. FAQ for evaluation-stage readers",
            "6. Final recommendation and CTA",
        ]
        headings = [
            f"Best {subject.title()}",
            f"How to Compare {subject.title()}",
            "Features That Matter Most",
            "Best Options by Use Case",
            "Frequently Asked Questions",
        ]
    else:
        outline = [
            f"1. Introduction to {query}",
            f"2. Why {query} matters",
            f"3. Core concepts and examples",
            "4. Recommended structure for the article",
            "5. FAQ section answering the main questions",
            "6. Conclusion and next-step CTA",
        ]
        headings = [
            f"What Is {topic}?",
            f"Why {topic} Matters",
            f"How to Cover {topic} in Depth",
            "Best Practices and Examples",
            "Frequently Asked Questions",
        ]

    return SEOContentBrief(
        targetIntent=insights.searchIntent,
        contentOutline="\n".join(outline),
        recommendedHeadings=headings,
        keyEntitiesToMention=[term.title() for term in (_phrases(" ".join(insights.relatedKeywords), 8) or ["search intent", "primary keyword"])],
        faqSuggestions=insights.relatedQuestions[:6] or [f"What is {query}?", f"How do I optimize for {query}?"],
        keywordPlacementGuidance=(
            f"Use {query} in the title, H1, opening paragraph, and at least one subheading. "
            "Add related phrases naturally in examples, FAQs, and image alt text."
        ),
        contentStructureRecommendations=(
            "Keep the page scannable with short sections, examples, and a compact FAQ. "
            "Lead with the answer, then expand with supporting detail."
        ),
        writingGuidelines=(
            "Write clearly, stay specific, and prioritize helpfulness over keyword repetition. "
            "Use trustworthy examples and keep the tone practical."
        ),
    )


def _fallback_audit(title: str, content: str, query: str, insights: SEOSearchInsights) -> SEOArticleAudit:
    word_count = _word_count(content)
    headings = [line for line in content.splitlines() if re.match(r"^(#{1,6}|\d+\.)\s+", line.strip())]
    keyword_hits = sum(content.lower().count(keyword.lower()) for keyword in insights.primaryKeywords[:3])
    missing = []
    if not headings:
        missing.append("Clear heading hierarchy")
    if word_count < 500:
        missing.append("More depth and supporting detail")
    if keyword_hits < 2:
        missing.append("Better integration of primary keywords")
    if not _extract_questions(content, query):
        missing.append("FAQ section answering user questions")

    return SEOArticleAudit(
        contentStrengths=(
            "The draft can be turned into a useful SEO asset if we strengthen the opening, section flow, and examples."
        ),
        contentGaps=(
            "It needs clearer sectioning, stronger examples, and a more explicit answer to the main search intent."
        ),
        keywordOpportunities=(
            f"Expand around {', '.join(insights.relatedKeywords[:4]) or query} with dedicated sections and long-tail phrasing."
        ),
        structureImprovements=(
            "Add a stronger intro, logical H2/H3 hierarchy, summary callout, and a closing FAQ or key takeaways section."
        ),
        e_e_a_t_assessment=(
            "Improve trust by naming sources, adding examples, and avoiding unsupported claims."
        ),
        missingSections=missing or ["FAQ section", "Improved heading hierarchy"],
        prioritizedRecommendations=[
            "Add a concise summary section that answers the search intent immediately.",
            "Use the primary keyword in the title, intro, and at least one subheading.",
            "Expand the body with practical examples, context, and clearer explanations.",
            "Add FAQ coverage for the most common related questions.",
        ],
    )


def _fallback_edits(title: str, query: str, audit: SEOArticleAudit, insights: SEOSearchInsights) -> SEOSectionEdits:
    return SEOSectionEdits(
        improvedSections=[
            {
                "heading": f"Introduction to {query.title()}",
                "rewrite": f"{title or query.title()} should open by answering the user's main question and framing why {query} matters.",
            },
            {
                "heading": "Core Takeaways",
                "rewrite": "Summarize the key points in 3-5 bullets so readers can scan the page quickly.",
            },
            {
                "heading": "FAQ",
                "rewrite": "Answer the most common related questions directly with clear, concise language.",
            },
        ],
        keywordIntegrationSummary=(
            f"Primary keywords to reinforce: {', '.join(insights.primaryKeywords[:3])}. "
            "Use related keywords naturally across the body and headings."
        ),
        changesExplanation=(
            "These rewrites improve scannability, topical coverage, and keyword placement without making the page feel stuffed."
        ),
    )


def _sections(search: SEOSearchInsights, brief: SEOContentBrief | None, audit: SEOArticleAudit | None, edits: SEOSectionEdits | None) -> list[SEOReportSection]:
    out = [
        SEOReportSection(
            title="Search Insights",
            summary=search.searchIntent,
            bullets=[
                f"Primary keywords: {', '.join(search.primaryKeywords[:5])}" if search.primaryKeywords else "Primary keywords were inferred from the input.",
                f"Related keywords: {', '.join(search.relatedKeywords[:5])}" if search.relatedKeywords else "Related keywords were inferred from topical context.",
                f"Questions: {', '.join(search.relatedQuestions[:4])}" if search.relatedQuestions else "Related questions were inferred from topical context.",
            ],
            kind="insights",
        )
    ]
    if brief:
        out.append(
            SEOReportSection(
                title="Content Brief",
                summary=brief.targetIntent,
                bullets=[brief.contentStructureRecommendations, brief.keywordPlacementGuidance, brief.writingGuidelines],
                kind="brief",
            )
        )
    if audit:
        out.append(
            SEOReportSection(
                title="Article Audit",
                summary=audit.contentGaps,
                bullets=[audit.contentStrengths, audit.keywordOpportunities, audit.structureImprovements, audit.e_e_a_t_assessment],
                kind="audit",
            )
        )
    if edits:
        out.append(
            SEOReportSection(
                title="Section Rewrites",
                summary=edits.changesExplanation,
                bullets=[item.get("heading", "") for item in edits.improvedSections if item.get("heading")],
                kind="rewrite",
            )
        )
    return out


def _gemini_prompt(schema_name: str, query: str, mode: str, title: str | None, article_text: str, serp: SEOSearchInsights) -> str:
    return "\n".join(
        [
            "You are an expert SEO strategist.",
            "Return STRICT JSON only. No markdown, no explanation, no code fences.",
            f"Schema: {schema_name}",
            f"Mode: {mode}",
            f"Query: {query}",
            f"Title: {title or ''}",
            f"Article text: {article_text[:12000]}",
            "Search insights:",
            json.dumps(serp.model_dump(mode="json"), ensure_ascii=True),
            "",
            "For brief mode, return keys: searchInsights, contentBrief, reportSections, nextSteps, summary, warnings.",
            "For optimization mode, return keys: searchInsights, articleAudit, sectionEdits, reportSections, nextSteps, summary, warnings.",
            "reportSections MUST be an array of objects with exactly these keys: title, summary, bullets, kind.",
            "Do not use heading/body for reportSections. Use title/summary instead.",
            "sectionEdits.improvedSections MUST be an array of objects with exactly these keys: heading, rewrite.",
            "Use short, actionable prose and preserve the requested field casing exactly.",
        ]
    )


def _is_transient_gemini_status(status_code: int) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


async def _call_gemini_json(prompt: str) -> tuple[dict[str, Any] | None, str | None]:
    if not SETTINGS.api_key:
        return None, "LLM enrichment is unavailable because GEMINI_API_KEY is not configured."
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    last_status: int | None = None
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in SETTINGS.fallback_models or (SETTINGS.model,):
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(3):
                try:
                    resp = await client.post(f"{endpoint}?key={SETTINGS.api_key}", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        return None, "LLM enrichment returned no candidates, so the SEO agent used its deterministic fallback."
                    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
                    text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
                    start = text.find("{")
                    end = text.rfind("}")
                    if start == -1 or end == -1 or end <= start:
                        return None, "LLM enrichment returned an unexpected format, so the SEO agent used its deterministic fallback."
                    parsed = json.loads(text[start : end + 1])
                    if not isinstance(parsed, dict):
                        return None, "LLM enrichment returned an unexpected payload shape, so the SEO agent used its deterministic fallback."
                    return parsed, None
                except httpx.HTTPStatusError as exc:  # pragma: no cover - external dependency
                    last_error = exc
                    last_status = exc.response.status_code
                    logger.warning(
                        "Gemini analysis failed for model %s (attempt %s/3): %s",
                        model,
                        attempt + 1,
                        exc,
                    )
                    if last_status == 403:
                        return None, "LLM enrichment is currently unavailable because the configured Gemini key was rejected with 403."
                    if _is_transient_gemini_status(last_status) and attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    if _is_transient_gemini_status(last_status):
                        break
                    return None, f"LLM enrichment is currently unavailable ({last_status}), so the SEO agent used its deterministic fallback."
                except Exception as exc:  # pragma: no cover - external dependency
                    last_error = exc
                    logger.warning(
                        "Gemini analysis failed for model %s (attempt %s/3): %s",
                        model,
                        attempt + 1,
                        exc,
                    )
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break

    if last_status is not None:
        return None, f"LLM enrichment is currently unavailable ({last_status}), so the SEO agent used its deterministic fallback."
    if last_error is not None:
        return None, "LLM enrichment is currently unavailable, so the SEO agent used its deterministic fallback."
    return None, "LLM enrichment is currently unavailable, so the SEO agent used its deterministic fallback."


def _sanitize_action(action: str | None) -> str:
    return (action or "").strip().lower()


def _is_brief_action(action: str) -> bool:
    return action in {"generate_brief", "brief", "topic_only", "analyze_seo", "run_seo_agent"}


def _is_optimization_action(action: str) -> bool:
    return action in {"optimize_article", "optimize_url", "rewrite_content", "optimize_content", "audit", "run_seo_agent"}


def _needs_input_response(action: str, missing_fields: list[str], guidance: str) -> SEOActionResponse:
    cleaned_missing = _dedupe(missing_fields)
    summary = "I need a little more article input before I can run that SEO workflow."
    return SEOActionResponse(
        status="needs_input",
        type="seo_result",
        displayName="SEO Agent",
        message=guidance,
        summary=summary,
        result={
            "missing_fields": cleaned_missing,
            "action": action,
            "summary": summary,
            "guidance": guidance,
        },
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _split_list_like_text(text: str) -> list[str]:
    cleaned = _clean(text)
    if not cleaned:
        return []
    parts = [_clean(part) for part in re.split(r"(?:\r?\n+|[•·;])", cleaned) if _clean(part)]
    return parts or [cleaned]


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, (int, float, bool)):
        return _clean(str(value))
    if isinstance(value, list):
        return _clean(" ".join(filter(None, (_coerce_text(item) for item in value))))
    if isinstance(value, dict):
        for key in (
            "summary",
            "description",
            "text",
            "body",
            "content",
            "value",
            "answer",
            "rewrite",
            "title",
            "heading",
            "name",
            "label",
            "intent",
        ):
            text = _coerce_text(value.get(key))
            if text:
                return text
    return ""


def _coerce_text_list(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                text = _coerce_text(
                    item.get("text")
                    or item.get("summary")
                    or item.get("title")
                    or item.get("heading")
                    or item.get("question")
                    or item.get("query")
                    or item.get("rewrite")
                    or item
                )
            else:
                text = _coerce_text(item)
            if text:
                items.extend(_split_list_like_text(text))
        return _dedupe(items)
    if isinstance(value, dict):
        for item in value.values():
            text = _coerce_text(item)
            if text:
                items.extend(_split_list_like_text(text))
        return _dedupe(items)
    text = _coerce_text(value)
    return _dedupe(_split_list_like_text(text)) if text else []


def _normalize_search_insights_payload(value: Any) -> dict[str, Any]:
    payload = _as_mapping(value)
    return {
        "primaryKeywords": _coerce_text_list(
            payload.get("primaryKeywords") or payload.get("keywords") or payload.get("targetKeywords")
        ),
        "relatedKeywords": _coerce_text_list(
            payload.get("relatedKeywords") or payload.get("semanticKeywords") or payload.get("supportingKeywords")
        ),
        "relatedQuestions": _coerce_text_list(
            payload.get("relatedQuestions") or payload.get("peopleAlsoAsk") or payload.get("questions") or payload.get("faqSuggestions")
        ),
        "searchIntent": _coerce_text(payload.get("searchIntent") or payload.get("intent") or payload.get("targetIntent")),
        "competitorAnalysis": _coerce_text(
            payload.get("competitorAnalysis") or payload.get("competitorNotes") or payload.get("competitiveLandscape")
        ),
        "aiOverviewSummary": _coerce_text(
            payload.get("aiOverviewSummary") or payload.get("aiOverview") or payload.get("overviewSummary") or payload.get("summary")
        ),
        "topResultTitles": _coerce_text_list(
            payload.get("topResultTitles") or payload.get("competitorTitles") or payload.get("serpTitles")
        ),
        "topDomains": _coerce_text_list(payload.get("topDomains") or payload.get("competitorDomains") or payload.get("domains")),
    }


def _normalize_content_brief_payload(value: Any) -> dict[str, Any]:
    payload = _as_mapping(value)
    return {
        "targetIntent": _coerce_text(payload.get("targetIntent") or payload.get("searchIntent") or payload.get("intent")),
        "contentOutline": _coerce_text(payload.get("contentOutline") or payload.get("outline")),
        "recommendedHeadings": _coerce_text_list(
            payload.get("recommendedHeadings") or payload.get("headings") or payload.get("h1H2s")
        ),
        "keyEntitiesToMention": _coerce_text_list(
            payload.get("keyEntitiesToMention") or payload.get("entitiesToMention") or payload.get("entities")
        ),
        "faqSuggestions": _coerce_text_list(payload.get("faqSuggestions") or payload.get("faqIdeas") or payload.get("faqs")),
        "keywordPlacementGuidance": _coerce_text(
            payload.get("keywordPlacementGuidance") or payload.get("keywordGuidance")
        ),
        "contentStructureRecommendations": _coerce_text(
            payload.get("contentStructureRecommendations") or payload.get("structureRecommendations")
        ),
        "writingGuidelines": _coerce_text(
            payload.get("writingGuidelines") or payload.get("styleGuidelines") or payload.get("editorialGuidelines")
        ),
    }


def _normalize_article_audit_payload(value: Any) -> dict[str, Any]:
    payload = _as_mapping(value)
    return {
        "contentStrengths": _coerce_text(payload.get("contentStrengths") or payload.get("strengths")),
        "contentGaps": _coerce_text(payload.get("contentGaps") or payload.get("gaps")),
        "keywordOpportunities": _coerce_text(
            payload.get("keywordOpportunities") or payload.get("keywordGaps") or payload.get("keywordSuggestions")
        ),
        "structureImprovements": _coerce_text(
            payload.get("structureImprovements") or payload.get("structureSuggestions")
        ),
        "e_e_a_t_assessment": _coerce_text(
            payload.get("e_e_a_t_assessment") or payload.get("eeatAssessment") or payload.get("eEATAssessment")
        ),
        "missingSections": _coerce_text_list(payload.get("missingSections") or payload.get("sectionGaps")),
        "prioritizedRecommendations": _coerce_text_list(
            payload.get("prioritizedRecommendations") or payload.get("recommendations") or payload.get("nextActions")
        ),
    }


def _normalize_section_edits_payload(value: Any) -> dict[str, Any]:
    payload = _as_mapping(value)
    raw_sections = payload.get("improvedSections") or payload.get("sectionEdits") or payload.get("rewrites") or []
    improved_sections: list[dict[str, str]] = []
    if isinstance(raw_sections, list):
        for index, item in enumerate(raw_sections, start=1):
            if not isinstance(item, dict):
                text = _coerce_text(item)
                if text:
                    improved_sections.append({"heading": f"Section {index}", "rewrite": text})
                continue
            heading = _coerce_text(item.get("heading") or item.get("title") or item.get("section") or item.get("name"))
            rewrite = _coerce_text(
                item.get("rewrite") or item.get("summary") or item.get("body") or item.get("content") or item.get("text")
            )
            if heading or rewrite:
                improved_sections.append({"heading": heading or f"Section {index}", "rewrite": rewrite or heading or ""})
    return {
        "improvedSections": improved_sections,
        "keywordIntegrationSummary": _coerce_text(
            payload.get("keywordIntegrationSummary") or payload.get("keywordSummary")
        ),
        "changesExplanation": _coerce_text(
            payload.get("changesExplanation") or payload.get("rewriteRationale") or payload.get("explanation")
        ),
    }


def _normalize_report_section_payload(value: Any, index: int) -> dict[str, Any] | None:
    if isinstance(value, str):
        text = _clean(value)
        if not text:
            return None
        return {"title": f"Section {index}", "summary": text, "bullets": [], "kind": "section"}
    payload = _as_mapping(value)
    if not payload:
        return None
    title = _coerce_text(payload.get("title") or payload.get("heading") or payload.get("sectionTitle") or payload.get("name"))
    summary = _coerce_text(
        payload.get("summary") or payload.get("body") or payload.get("description") or payload.get("content") or payload.get("text")
    )
    bullets = _coerce_text_list(
        payload.get("bullets")
        or payload.get("bulletPoints")
        or payload.get("keyPoints")
        or payload.get("points")
        or payload.get("items")
        or payload.get("recommendations")
    )
    if not summary and bullets:
        summary = bullets[0]
        bullets = bullets[1:]
    if not title and summary:
        title = f"Section {index}"
    if not title:
        return None
    return {
        "title": title,
        "summary": summary or title,
        "bullets": bullets,
        "kind": _clean(str(payload.get("kind") or payload.get("type") or "section")).lower() or "section",
    }


def _normalize_report_sections(value: Any) -> list[SEOReportSection]:
    if not isinstance(value, list):
        return []
    sections: list[SEOReportSection] = []
    for index, item in enumerate(value, start=1):
        payload = _normalize_report_section_payload(item, index)
        if not payload:
            continue
        sections.append(SEOReportSection(**payload))
    return sections


async def run_seo_analysis(request: SEOActionRequest) -> SEOActionResponse:
    action = _sanitize_action(request.action)
    if action not in {
        "generate_brief", "optimize_article", "optimize_url", "rewrite_content", "optimize_content", "audit", "brief", "topic_only", "analyze_seo", "run_seo_agent",
    }:
        return SEOActionResponse(
            status="failed",
            type="seo_result",
            displayName="SEO Agent",
            message="seo-agent supports generate_brief, optimize_article, optimize_url, audit, rewrite_content, and run_seo_agent.",
            error=f"Unknown action: {request.action}",
        )

    raw_title = _clean(request.title or "")
    raw_content = _clean(request.content or "")
    raw_url = _clean(request.url or "")
    raw_topic = _clean(request.topic or "")
    raw_query = _clean(request.query or "")

    warnings: list[str] = []
    if raw_url and not _sanitize_url(raw_url):
        warnings.append("The supplied URL looked invalid or placeholder-like, so it was ignored.")
    if raw_title and _looks_like_placeholder(raw_title):
        warnings.append("The supplied title looked like placeholder text, so it was ignored.")
    if raw_content and _looks_like_placeholder(raw_content):
        warnings.append("The supplied content looked like placeholder text, so it was ignored.")
    if not (os.getenv("SERPAPI_API_KEY") or "").strip():
        warnings.append("Live SERP data is unavailable because SerpApi is not configured, so search insights were inferred heuristically.")

    source_url = _sanitize_url(request.url)
    title = _sanitize_optional_text(request.title) or None
    content = _sanitize_optional_text(request.content)
    topic = _infer_topic(request)
    query = _build_query(topic, title, content)
    has_article_text = _has_meaningful_article_text(content)
    input_mode = "url_article" if source_url else ("title_content" if title and has_article_text else "topic_only")
    extracted = ""

    if _is_brief_action(action) and not any([source_url, title, has_article_text, raw_topic, raw_query]):
        return _needs_input_response(
            action,
            ["topic or target keyword"],
            "Share a topic, target keyword, article URL, or draft text so I can build a useful SEO brief.",
        )

    if action in {"audit", "optimize_url"} and not source_url and not (title and has_article_text):
        return _needs_input_response(
            action,
            ["article URL or article title plus body text"],
            "To run an SEO audit, share a live article URL or paste the article title together with the body text.",
        )

    if action in {"optimize_article", "rewrite_content", "optimize_content"} and not source_url and not has_article_text:
        return _needs_input_response(
            action,
            ["article draft text or live URL"],
            "To optimize or rewrite content, paste the draft body or share the live article URL.",
        )

    if source_url:
        extracted = _fetch_url_text(source_url)
        if extracted:
            content = extracted
            input_mode = "url_article"
            if not title:
                title = f"Article from {_normalize_domain(source_url)}"
        else:
            warnings.append("The URL could not be fully extracted, so the analysis used the provided text fields instead.")

    article_text = content[:12000]
    serp = _collect_serp(query, article_text)
    mode = "brief"
    should_optimize = bool(source_url or (title and has_article_text)) and _is_optimization_action(action)

    gemini_json, gemini_warning = await _call_gemini_json(
        _gemini_prompt("SEOAnalysis", query, "optimization" if should_optimize else "brief", title, article_text, serp)
    )
    if gemini_warning:
        warnings.append(gemini_warning)
    if gemini_json:
        try:
            search_payload = _normalize_search_insights_payload(gemini_json.get("searchInsights")) or serp.model_dump(mode="json")
            search = SEOSearchInsights(**search_payload)
            brief = None
            audit = None
            edits = None
            if should_optimize:
                mode = "optimization"
                audit_payload = _normalize_article_audit_payload(gemini_json.get("articleAudit"))
                edits_payload = _normalize_section_edits_payload(gemini_json.get("sectionEdits"))
                audit = SEOArticleAudit(**audit_payload) if any(audit_payload.values()) else _fallback_audit(title or query, article_text, query, search)
                edits = SEOSectionEdits(**edits_payload) if any(edits_payload.values()) else _fallback_edits(title or query, query, audit, search)
            else:
                brief_payload = _normalize_content_brief_payload(gemini_json.get("contentBrief"))
                brief = SEOContentBrief(**brief_payload) if any(brief_payload.values()) else _fallback_brief(query, search)
            report_sections = _normalize_report_sections(gemini_json.get("reportSections"))
            if not report_sections:
                report_sections = _sections(search, brief, audit, edits)
            summary = _clean(str(gemini_json.get("summary") or ("SEO audit generated." if should_optimize else "SEO content brief generated.")))
            next_steps = _coerce_text_list(gemini_json.get("nextSteps"))
            if not next_steps:
                next_steps = [
                    "Review the search insights and turn them into your working outline.",
                    "Apply the recommended structure and keyword guidance while drafting.",
                ]
            warnings.extend(_coerce_text_list(gemini_json.get("warnings")))
            result = SEOAnalysisResult(
                mode=mode,
                inputMode=input_mode,
                topic=topic,
                searchQuery=query,
                title=title,
                sourceUrl=source_url,
                extractedArticle=article_text[:8000] if article_text else None,
                sourceWordCount=_word_count(article_text),
                sourceCharacterCount=len(article_text),
                warnings=warnings,
                searchInsights=search,
                contentBrief=brief,
                articleAudit=audit,
                sectionEdits=edits,
                reportSections=report_sections,
                nextSteps=next_steps,
                summary=summary,
                metadata={
                    "action": action,
                    "hasArticleInput": bool(source_url or (title and has_article_text)),
                    "geminiConfigured": True,
                    "geminiUsed": True,
                    "serpApiConfigured": bool((os.getenv("SERPAPI_API_KEY") or "").strip()),
                },
            )
            return SEOActionResponse(
                status="success",
                type="seo_result",
                message="SEO analysis generated successfully.",
                summary=summary,
                result=result.model_dump(mode="json"),
                displayName="SEO Agent",
            )
        except Exception as exc:
            logger.warning("Gemini payload normalization failed: %s", exc)

    search = serp
    if should_optimize:
        mode = "optimization"
        audit = _fallback_audit(title or query, article_text, query, search)
        edits = _fallback_edits(title or query, query, audit, search)
        brief = None
    else:
        brief = _fallback_brief(query, search)
        audit = None
        edits = None

    report_sections = _sections(search, brief, audit, edits)
    summary = (
        f"SEO audit generated for {query}. The page needs sharper structure, stronger keyword coverage, and clearer E-E-A-T signals."
        if audit
        else f"SEO brief generated for {query}. Focus on the target intent, the recommended headings, and the FAQ section."
    )
    next_steps = [
        "Review the recommended headings and turn them into your outline.",
        "Use the keyword placement guidance when drafting the opening and subheads.",
        "Add an FAQ section that directly answers the highest-value questions.",
    ]
    if audit and edits:
        next_steps = [
            "Apply the article audit recommendations in order of priority.",
            "Replace weak sections with the suggested rewrites and tighten the intro.",
            "Add supporting examples, citations, and a clearer FAQ to raise trust and depth.",
        ]

    result = SEOAnalysisResult(
        mode=mode,
        inputMode=input_mode,
        topic=topic,
        searchQuery=query,
        title=title,
        sourceUrl=source_url,
        extractedArticle=article_text[:8000] if article_text else None,
        sourceWordCount=_word_count(article_text),
        sourceCharacterCount=len(article_text),
        warnings=warnings,
        searchInsights=search,
        contentBrief=brief,
        articleAudit=audit,
        sectionEdits=edits,
        reportSections=report_sections,
        nextSteps=next_steps,
        summary=summary,
        metadata={
            "action": action,
            "hasArticleInput": bool(source_url or (title and has_article_text)),
            "geminiConfigured": bool(SETTINGS.api_key),
            "geminiUsed": False,
            "serpApiConfigured": bool((os.getenv("SERPAPI_API_KEY") or "").strip()),
        },
    )
    return SEOActionResponse(
        status="success",
        type="seo_result",
        message="SEO analysis generated successfully.",
        summary=summary,
        result=result.model_dump(mode="json"),
        displayName="SEO Agent",
    )

