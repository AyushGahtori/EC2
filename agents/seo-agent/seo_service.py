from __future__ import annotations

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


@dataclass
class GeminiSettings:
    api_key: str = (os.getenv("GEMINI_API_KEY") or "").strip()
    model: str = (
        os.getenv("GEMINI_MODEL_FLASH")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GEMINI_MODEL_PRO")
        or "gemini-2.5-flash"
    ).strip()


SETTINGS = GeminiSettings()


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']+", text.lower())


def _phrases(text: str, limit: int = 8) -> list[str]:
    words = [token for token in _tokens(text) if token not in STOPWORDS and len(token) > 2]
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


def _extract_questions(text: str, seed: str, limit: int = 6) -> list[str]:
    qs = [s for s in _extract_sentences(text) if s.endswith("?")]
    if qs:
        return qs[:limit]
    return [f"What should readers know about {seed}?", f"How do I optimize content for {seed}?"][:limit]


def _infer_topic(request: SEOActionRequest) -> str:
    for candidate in (request.topic, request.query, request.title):
        if candidate and candidate.strip():
            return candidate.strip()
    if request.content:
        phrases = _phrases(request.content, 4)
        if phrases:
            return " ".join(phrases[:3]).strip()
    return "seo content optimization"


def _build_query(topic: str, title: str | None, content: str | None) -> str:
    if title and title.strip():
        return title.strip()
    if topic:
        return topic.strip()
    if content:
        phrases = _phrases(content, 4)
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
    seed = _phrases(" ".join(filter(None, [query, article_text or ""])), 5) or [query]
    related = [f"{term} guide" for term in seed[:3]] + [f"{term} tips" for term in seed[:3]]
    return {
        "organic_results": [],
        "related_questions": [{"question": f"What is {seed[0]}?"}, {"question": f"How do I improve {seed[0]} SEO?"}],
        "related_searches": [{"query": item} for item in related[:8]],
        "ai_overview": {"answer": f"Readers want practical guidance around {seed[0]}."},
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

    related = []
    for item in (ai_mode.get("related_searches") or google.get("related_searches") or [])[:10]:
        text = _clean(str(item.get("query") if isinstance(item, dict) else item))
        if text:
            related.append(text)
    if not related:
        related = [f"{query} examples", f"{query} best practices", f"{query} guide"]

    overview = ai_mode.get("ai_overview") or google.get("ai_overview") or {}
    if isinstance(overview, dict):
        overview_summary = _clean(str(overview.get("answer") or overview.get("summary") or overview.get("text") or ""))
    else:
        overview_summary = _clean(str(overview))
    if not overview_summary:
        overview_summary = f"Searchers want practical, trustworthy guidance around {query}."

    intent = "Informational - readers want a focused SEO brief or optimization plan."
    lower = f"{query} {article_text or ''}".lower()
    if any(token in lower for token in ("price", "pricing", "cost", "best", "compare", "tool", "software")):
        intent = "Commercial investigation - readers are comparing options and looking for decision support."
    if any(token in lower for token in ("optimize", "audit", "rewrite", "improve")):
        intent = "Transactional - readers want direct optimization guidance for existing content."

    competitor = (
        "Top results usually favor concise explainers, list posts, and pages that answer the main question quickly "
        "while proving trust with examples and clear structure."
    )
    if top_domains:
        competitor += f" Common domains include {', '.join(top_domains[:4])}."

    primary = [query] + [p.replace("-", " ") for p in _phrases(query, 4)]
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
    return SEOContentBrief(
        targetIntent=insights.searchIntent,
        contentOutline="\n".join(
            [
                f"1. Introduction to {query}",
                f"2. Why {query} matters",
                f"3. Core concepts and examples",
                "4. Recommended structure for the article",
                "5. FAQ section answering the main questions",
                "6. Conclusion and next-step CTA",
            ]
        ),
        recommendedHeadings=[
            f"What Is {topic}?",
            f"Why {topic} Matters",
            f"How to Cover {topic} in Depth",
            "Best Practices and Examples",
            "Frequently Asked Questions",
        ],
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
            "Use short, actionable prose and preserve the requested field casing exactly.",
        ]
    )


async def _call_gemini_json(prompt: str) -> dict[str, Any] | None:
    if not SETTINGS.api_key:
        return None
    try:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{SETTINGS.model}:generateContent"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{endpoint}?key={SETTINGS.api_key}", json=payload)
            resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
        text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:  # pragma: no cover - external dependency
        logger.warning("Gemini analysis failed: %s", exc)
        return None


def _sanitize_action(action: str | None) -> str:
    return (action or "").strip().lower()


def _is_brief_action(action: str) -> bool:
    return action in {"generate_brief", "brief", "topic_only", "analyze_seo", "run_seo_agent"}


def _is_optimization_action(action: str) -> bool:
    return action in {"optimize_article", "optimize_url", "rewrite_content", "optimize_content", "audit", "run_seo_agent"}


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

    topic = _infer_topic(request)
    query = _build_query(topic, request.title, request.content)
    source_url = request.url.strip() if request.url and request.url.strip() else None
    title = request.title.strip() if request.title and request.title.strip() else None
    input_mode = "url_article" if source_url else ("title_content" if request.title and request.content else "topic_only")
    warnings: list[str] = []
    content = _clean(request.content or "")
    extracted = ""

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
    should_optimize = bool(source_url or (request.title and request.content)) and _is_optimization_action(action)

    gemini_json = await _call_gemini_json(_gemini_prompt("SEOAnalysis", query, "optimization" if should_optimize else "brief", title, article_text, serp))
    if gemini_json:
        try:
            search = SEOSearchInsights(**(gemini_json.get("searchInsights") or serp.model_dump(mode="json")))
            brief = None
            audit = None
            edits = None
            if should_optimize:
                mode = "optimization"
                audit = SEOArticleAudit(**(gemini_json.get("articleAudit") or {}))
                edits = SEOSectionEdits(**(gemini_json.get("sectionEdits") or {}))
            else:
                brief = SEOContentBrief(**(gemini_json.get("contentBrief") or {}))
            report_sections = [
                SEOReportSection(**item)
                for item in (gemini_json.get("reportSections") or [])
                if isinstance(item, dict)
            ]
            if not report_sections:
                report_sections = _sections(search, brief, audit, edits)
            summary = _clean(str(gemini_json.get("summary") or ("SEO audit generated." if should_optimize else "SEO content brief generated.")))
            next_steps = [str(item).strip() for item in (gemini_json.get("nextSteps") or []) if str(item).strip()]
            if not next_steps:
                next_steps = [
                    "Review the search insights and turn them into your working outline.",
                    "Apply the recommended structure and keyword guidance while drafting.",
                ]
            warnings.extend([str(item).strip() for item in (gemini_json.get("warnings") or []) if str(item).strip()])
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
                metadata={"action": action, "hasArticleInput": bool(source_url or (request.title and request.content)), "geminiConfigured": True, "serpApiConfigured": bool((os.getenv("SERPAPI_API_KEY") or "").strip())},
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
        metadata={"action": action, "hasArticleInput": bool(source_url or (request.title and request.content)), "geminiConfigured": bool(SETTINGS.api_key), "serpApiConfigured": bool((os.getenv("SERPAPI_API_KEY") or "").strip())},
    )
    return SEOActionResponse(
        status="success",
        type="seo_result",
        message="SEO analysis generated successfully.",
        summary=summary,
        result=result.model_dump(mode="json"),
        displayName="SEO Agent",
    )

