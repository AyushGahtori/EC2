"""
Marketing agent tools.

Every tool here is callable by the LLM through LangChain's tool-calling interface.
The LLM decides WHICH tools to call, WHEN, and with WHAT arguments — zero hardcoded logic.

Tools available:
  • analyze_product_image       — Vision LLM analyses uploaded product photo
  • generate_marketing_copy     — Writes descriptions, headlines, taglines
  • generate_social_media_post  — Platform-optimised posts (IG/LinkedIn/Twitter/etc)
  • research_poster_inspiration — Finds safe design/template inspiration for posters
  • generate_html_poster        — Full HTML/CSS promotional poster (renderable)
  • generate_hashtags           — Platform-aware hashtag sets
  • get_product_context         — Retrieves stored product info from session
  • save_generated_content      — Persists content to MongoDB
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional, Union

from langchain_core.tools import tool


def _as_bool(v, default: bool = True) -> bool:
    """Coerce a value to bool, tolerating the string forms some LLMs emit
    ("true"/"false"/"yes"/"no"/"1"/"0") instead of JSON booleans."""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1", "on"):
            return True
        if s in ("false", "no", "n", "0", "off", ""):
            return False
    return default

logger = logging.getLogger(__name__)

# ─── Shared helper ────────────────────────────────────────────────────────────

async def _llm_call(system: str, user: str, image_b64: Optional[str] = None) -> str:
    """Fire a quick LLM call — imported lazily to avoid circular imports."""
    from services.llm_service import quick_completion
    return await quick_completion(system, user, image_b64=image_b64)


def _run_coro(coro):
    """Run an async coroutine from a synchronous context, wherever we are called from.

    LangGraph's ToolNode invokes sync tools via ``run_in_executor`` — i.e. on a worker
    thread that has no event loop. Python 3.12 made ``asyncio.get_event_loop()``
    raise in that case, so we probe for a running loop instead:

      - no running loop in this thread → safe to ``asyncio.run``
      - a loop IS running here          → dispatch to a fresh thread and run there
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


def _sync_llm(system: str, user: str, image_b64: Optional[str] = None) -> str:
    """Synchronous wrapper — tools are called synchronously by LangGraph's ToolNode."""
    return _run_coro(_llm_call(system, user, image_b64))


def _parse_product_analysis(product_analysis: Union[str, dict[str, Any]]) -> dict[str, Any]:
    """Parse product JSON from the agent, including DB-wrapped analysis payloads."""
    if isinstance(product_analysis, dict):
        data: dict[str, Any] = dict(product_analysis)
    else:
        try:
            data = json.loads(product_analysis or "{}")
        except Exception:
            return {"product_name": "Product", "raw": product_analysis or ""}

    # get_product_context may return {"analysis": "<json string>", ...}. Flatten it
    # so downstream prompts see the actual product fields, not an opaque string.
    inner = data.get("analysis")
    if isinstance(inner, str):
        try:
            inner_data = json.loads(inner)
            if isinstance(inner_data, dict):
                merged = dict(inner_data)
                for key in ("product_name", "name", "image_url"):
                    if key in data and key not in merged:
                        merged[key] = data[key]
                return merged
        except Exception:
            pass

    return data if data else {"product_name": "Product"}


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, (tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _clean_phrase(text: str) -> str:
    text = re.sub(r"[_/|]+", " ", text)
    text = re.sub(r"[^a-zA-Z0-9#&+.\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _dedupe_keep_order(items: list[str], limit: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _clean_phrase(str(item))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _product_search_terms(product_data: dict[str, Any], extra: str = "") -> list[str]:
    terms: list[str] = []
    for key in (
        "product_name",
        "name",
        "category",
        "subcategory",
        "style",
        "mood",
        "price_tier",
        "brand_aesthetic",
    ):
        terms.extend(_listify(product_data.get(key)))
    for key in (
        "colors",
        "materials",
        "key_features",
        "unique_selling_points",
        "marketing_angles",
        "search_keywords",
        "poster_design_keywords",
    ):
        terms.extend(_listify(product_data.get(key)))
    terms.extend(re.split(r"[,;]\s*|\s{2,}", extra or ""))
    return _dedupe_keep_order(terms, limit=14) or ["product"]


def _category_signal(product_data: dict[str, Any], extra: str = "") -> str:
    haystack = " ".join(_product_search_terms(product_data, extra)).lower()
    if any(k in haystack for k in (
        "burger", "pizza", "sandwich", "fries", "restaurant", "food",
        "meal", "cafe", "coffee", "drink", "beverage", "dessert", "cake",
    )):
        return "food"
    if any(k in haystack for k in (
        "shirt", "t-shirt", "tee", "hoodie", "dress", "sneaker", "shoe",
        "fashion", "clothing", "apparel", "streetwear", "jacket",
    )):
        return "fashion"
    if any(k in haystack for k in (
        "phone", "laptop", "headphone", "camera", "speaker", "watch",
        "gadget", "electronics", "device", "tech",
    )):
        return "tech"
    if any(k in haystack for k in (
        "skincare", "serum", "cream", "beauty", "cosmetic", "makeup",
        "perfume", "fragrance",
    )):
        return "beauty"
    return "general"


def _fallback_design_brief(
    product_data: dict[str, Any],
    poster_type: str = "promotional",
    extra: str = "",
) -> dict[str, Any]:
    """Built-in art-direction fallback when live web research is unavailable."""
    signal = _category_signal(product_data, extra)
    product_name = (
        product_data.get("product_name")
        or product_data.get("name")
        or product_data.get("subcategory")
        or "the product"
    )

    briefs: dict[str, dict[str, Any]] = {
        "food": {
            "creative_direction": (
                f"Appetite-first {poster_type} restaurant poster for {product_name}. "
                "Make the food feel close, hot, textured, and craveable."
            ),
            "layout_patterns": [
                "Oversized hero food photo cropped beyond the poster edges, not a small centered image.",
                "Dark charcoal or toasted warm background with radial glow behind the product.",
                "Huge condensed headline in the top third with deliberate overlap into the hero.",
                "Circular price, offer, or limited-time badge overlapping the product.",
                "Ingredient or benefit chips along one side, plus a clear bottom CTA bar.",
            ],
            "palette": [
                "charcoal or toasted brown base",
                "ketchup red accent",
                "mustard/golden yellow highlight",
                "cream or sesame typography",
            ],
            "typography": [
                "heavy condensed display headline",
                "bold geometric sans for offer badge",
                "small uppercase labels for ingredient chips",
            ],
            "photo_direction": [
                "Use the uploaded product photo as the only hero image when available.",
                "Crop tight, increase perceived scale, and frame with glow, plate, or sauce-like CSS shapes.",
            ],
            "copy_blocks": [
                "Short crave-led headline",
                "Sensory one-line tagline",
                "Offer or CTA such as Order Now, Grab The Bite, or Taste The Drop",
            ],
            "avoid": [
                "No bland centered flyer layout.",
                "No tiny food photo.",
                "No copying any referenced template exactly.",
            ],
        },
        "fashion": {
            "creative_direction": (
                f"Editorial fashion campaign poster for {product_name}, balancing product focus "
                "with premium brand whitespace."
            ),
            "layout_patterns": [
                "Asymmetric editorial grid with hero product image taking 55-70 percent of the canvas.",
                "Large art-directed headline with tight leading and small uppercase collection labels.",
                "Thin rule lines, style tags, and a restrained CTA to create premium polish.",
            ],
            "palette": [
                "product-derived neutrals",
                "one accent from product color",
                "soft off-white or graphite contrast",
            ],
            "typography": [
                "expressive serif or high-fashion display face",
                "clean sans for details and CTA",
            ],
            "photo_direction": [
                "Preserve the real product image; frame it like an editorial product shoot.",
            ],
            "copy_blocks": [
                "Collection-style eyebrow",
                "Specific product headline",
                "Material, fit, or styling benefit",
            ],
            "avoid": [
                "No generic e-commerce card.",
                "No unrelated lifestyle claims.",
            ],
        },
        "tech": {
            "creative_direction": (
                f"Premium launch poster for {product_name}, with futuristic depth and crisp hierarchy."
            ),
            "layout_patterns": [
                "Product hero floating on a gradient mesh or luminous technical grid.",
                "Metric/feature cards around the hero with strong spacing and alignment.",
                "Bottom CTA strip with product promise and action.",
            ],
            "palette": [
                "deep ink or clean white base",
                "electric cyan or lime accent",
                "soft glass highlights",
            ],
            "typography": [
                "modern geometric sans",
                "monospace micro-labels for technical details",
            ],
            "photo_direction": [
                "Keep reflections and shadows subtle; product should feel precise and premium.",
            ],
            "copy_blocks": [
                "Launch headline",
                "Three feature callouts",
                "CTA",
            ],
            "avoid": [
                "No cluttered sci-fi UI noise.",
                "No fake specs not present in product analysis.",
            ],
        },
        "beauty": {
            "creative_direction": (
                f"Elegant beauty poster for {product_name}, focused on texture, glow, and ritual."
            ),
            "layout_patterns": [
                "Hero product centered in a soft sculptural scene with organic shapes.",
                "Large graceful headline, airy spacing, and minimal detail cards.",
                "Subtle ingredient/benefit tags with a refined CTA.",
            ],
            "palette": [
                "cream or blush base",
                "soft botanical accent",
                "deep espresso text",
            ],
            "typography": [
                "refined serif headline",
                "minimal sans for supporting copy",
            ],
            "photo_direction": [
                "Make the product feel tactile with soft shadows and highlight rings.",
            ],
            "copy_blocks": [
                "Benefit-led headline",
                "Ritual or sensory tagline",
                "Shop or discover CTA",
            ],
            "avoid": [
                "No medical claims.",
                "No over-busy discount flyer style.",
            ],
        },
        "general": {
            "creative_direction": (
                f"Conversion-focused {poster_type} poster for {product_name}, built like a polished "
                "brand campaign rather than a generic flyer."
            ),
            "layout_patterns": [
                "One dominant hero visual with supporting typography arranged on an intentional grid.",
                "Clear headline, one benefit-led line, and one primary CTA.",
                "Layered background shapes, soft shadows, and product-derived accent color.",
            ],
            "palette": [
                "product-derived base",
                "one confident accent",
                "high-contrast text color",
            ],
            "typography": [
                "bold display headline",
                "clean readable sans for details",
            ],
            "photo_direction": [
                "Use the provided product image as the focal point and avoid empty placeholders.",
            ],
            "copy_blocks": [
                "Specific product headline",
                "One benefit or sensory promise",
                "CTA",
            ],
            "avoid": [
                "No generic centered template.",
                "No placeholder text or unrelated claims.",
            ],
        },
    }
    return briefs[signal]


def _get_ddgs_class():
    try:
        from ddgs import DDGS
        return DDGS, ""
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # fallback older package name
            return DDGS, ""
        except ImportError:
            return None, "ddgs package not installed. Run: pip install -r requirements.txt"


def _ddgs_text_search(query: str, count: int) -> tuple[list[dict[str, str]], str]:
    DDGS, err = _get_ddgs_class()
    if not DDGS:
        return [], err
    try:
        results: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for r in ddgs.text(
                query,
                region="wt-wt",
                safesearch="moderate",
                max_results=count,
            ):
                title = str(r.get("title") or "").strip()
                href = str(r.get("href") or r.get("url") or "").strip()
                body = str(r.get("body") or r.get("snippet") or "").strip()
                if not href.lower().startswith(("http://", "https://")):
                    continue
                results.append({"title": title, "url": href, "snippet": body[:220]})
                if len(results) >= count:
                    break
        return results, ""
    except Exception as e:
        logger.error(f"DDGS text search failed: {e}")
        return [], f"Web search failed: {e}"


def _ddgs_image_search(query: str, count: int) -> tuple[list[dict[str, str]], str]:
    DDGS, err = _get_ddgs_class()
    if not DDGS:
        return [], err
    try:
        results: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for r in ddgs.images(
                query,
                region="wt-wt",
                safesearch="moderate",
                size="Large",
                max_results=count * 2,
            ):
                url = str(r.get("image") or r.get("url") or "").strip()
                title = str(r.get("title") or "").strip()
                source = str(r.get("source") or r.get("url") or "").strip()
                if not url.lower().startswith(("http://", "https://")):
                    continue
                if url.lower().endswith(".svg"):
                    continue
                results.append({"title": title, "image_url": url, "source_url": source})
                if len(results) >= count:
                    break
        return results, ""
    except Exception as e:
        logger.error(f"DDGS image search failed: {e}")
        return [], f"Image search failed: {e}"


# ─── Product image analysis (shared) ──────────────────────────────────────────

_ANALYZE_SYSTEM_PROMPT = """You are an expert product analyst and visual merchandiser.
Analyse the product in the image and return a comprehensive JSON object with these fields:
{
  "product_name": "best guess at product name",
  "category": "clothing/accessory/electronics/etc",
  "subcategory": "t-shirt/hoodie/sneakers/etc",
  "colors": ["primary color", "secondary colors"],
  "materials": ["fabric/material type"],
  "style": "casual/formal/sporty/luxury/streetwear/etc",
  "gender_target": "men/women/unisex/kids",
  "age_group": "teens/young adults/adults/all ages",
  "key_features": ["feature 1", "feature 2"],
  "mood": "energetic/elegant/minimalist/bold/etc",
  "price_tier": "budget/mid-range/premium/luxury (guess)",
  "unique_selling_points": ["usp 1", "usp 2"],
  "brand_aesthetic": "description of brand vibe",
  "marketing_angles": ["angle 1", "angle 2", "angle 3"],
  "search_keywords": ["specific searchable product phrases for finding similar product photos"],
  "poster_design_keywords": ["specific poster/template style phrases relevant to the category"]
}
Return ONLY the JSON object, no other text.
If the image cannot be read, return {"error": "<reason>"} — never invent product details."""


def _looks_like_base64(s: str) -> bool:
    """Cheap sanity check — reject URLs, paths, and plainly-not-b64 strings so
    the LLM can't accidentally send us a URL as image_b64."""
    if not s or not isinstance(s, str):
        return False
    stripped = s.strip()
    if len(stripped) < 100:
        return False
    lower = stripped.lower()
    if lower.startswith(("http://", "https://", "data:", "/", "file:", "\\")):
        return False
    # Base64 alphabet is A-Z a-z 0-9 + / = (plus whitespace).
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r\t "
    )
    # Sample the first 512 chars — cheaper than scanning megabytes.
    for ch in stripped[:512]:
        if ch not in allowed:
            return False
    return True


async def analyze_image_async(image_b64: str, additional_context: str = "") -> str:
    """Async product-image analysis. Used both by the @tool wrapper and by the
    agent's pre-ReAct step so the main LLM never has to shuffle base64."""
    from services.llm_service import quick_completion

    if not _looks_like_base64(image_b64):
        return json.dumps({
            "error": "analyze_product_image requires the raw base64 bytes of the image, "
                     "not a URL or file path. The agent should not call this tool directly; "
                     "the backend pre-analyses uploaded images and provides the result as "
                     "[PRODUCT CONTEXT].",
        })

    user = f"Analyse this product image.{' Additional context: ' + additional_context if additional_context else ''}"
    try:
        result = await quick_completion(_ANALYZE_SYSTEM_PROMPT, user, image_b64=image_b64)
    except Exception as e:
        logger.error(f"analyze_image_async failed: {e}")
        return json.dumps({"error": str(e)})

    try:
        json.loads(result)
        return result
    except json.JSONDecodeError:
        return json.dumps({"raw_analysis": result, "product_name": "Product", "category": "unknown"})


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool
def analyze_product_image(
    image_b64: str,
    additional_context: str = "",
) -> str:
    """
    Analyse a product image using a vision-capable LLM.
    Returns a detailed JSON-structured description of the product including:
    name, category, colors, materials, style, target_audience, key_features, mood.

    Note: in the normal flow the agent pre-analyses the image before entering
    the ReAct loop and injects the result as product context, so the LLM
    usually does NOT need to call this tool. It remains available for retries
    or for when the user uploads a second image mid-conversation.

    Args:
        image_b64: Base64-encoded image string
        additional_context: Any extra context the user provided about the product
    """
    return _run_coro(analyze_image_async(image_b64, additional_context))


@tool
def generate_marketing_copy(
    product_analysis: str,
    copy_type: str = "full_description",
    tone: str = "professional",
    target_audience: str = "",
    word_count: int = 150,
    additional_instructions: str = "",
) -> str:
    """
    Generate compelling marketing copy for a product.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        copy_type: One of: full_description | headline | tagline | bullet_points | email | ad_copy
        tone: professional | casual | luxury | playful | urgent | inspirational | minimalist
        target_audience: Who this copy is aimed at (e.g., "young professionals aged 25-35")
        word_count: Approximate desired word count
        additional_instructions: Any special requirements from the user

    Returns:
        Marketing copy text ready to use
    """
    system = """You are an award-winning marketing copywriter who has worked with top global brands.
You craft copy that converts — emotionally resonant, benefit-focused, and brand-authentic.
Write copy that feels human, not AI-generated. Avoid clichés like "elevate your game" or "unleash your potential"."""

    user = f"""Product Analysis:
{product_analysis}

Task: Generate {copy_type} marketing copy.
Tone: {tone}
Target Audience: {target_audience or 'General consumer market'}
Approximate Word Count: {word_count}
{f'Special Instructions: {additional_instructions}' if additional_instructions else ''}

Deliver only the copy itself — no explanations, no "Here's the copy:" preamble."""

    return _sync_llm(system, user)


@tool
def generate_social_media_post(
    product_analysis: str,
    platform: str,
    campaign_angle: str = "",
    include_hashtags: Union[bool, str] = True,
    include_emoji: Union[bool, str] = True,
    call_to_action: str = "",
) -> str:
    """
    Generate a platform-optimised social media post for a product.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        platform: instagram | linkedin | twitter | facebook | pinterest | tiktok
        campaign_angle: Specific angle e.g., "summer launch", "limited edition drop", "behind the scenes"
        include_hashtags: Whether to include hashtags
        include_emoji: Whether to include emojis
        call_to_action: Specific CTA e.g., "Link in bio", "DM us", "Shop now"

    Returns:
        Complete social media post text ready to copy-paste
    """
    platform_guides = {
        "instagram": "Visual-first, story-driven, 3-5 line caption, strong first line, emojis are essential, 20-30 hashtags in first comment or end",
        "linkedin": "Professional yet personal, thought leadership angle, 150-300 words, minimal hashtags (3-5), no excessive emojis, add value/insight",
        "twitter": "Punchy, max 280 chars, witty, relatable, 1-2 hashtags max, optional emoji, must hook in first 5 words",
        "facebook": "Conversational, community-building, can be longer, question to drive comments, light hashtags",
        "pinterest": "Keyword-rich description, 100-300 chars, describe the visual, inspire action, 2-5 hashtags",
        "tiktok": "Casual Gen-Z tone, references trends, 150 chars caption, 3-5 trending hashtags, call to watch/share",
    }

    include_hashtags = _as_bool(include_hashtags, default=True)
    include_emoji = _as_bool(include_emoji, default=True)

    guide = platform_guides.get(platform.lower(), platform_guides["instagram"])

    system = f"""You are a social media marketing expert specialising in {platform} content strategy.
Platform-specific guidelines: {guide}
Write content that feels native to the platform — not corporate, not templated."""

    user = f"""Product Analysis:
{product_analysis}

Platform: {platform.upper()}
Campaign Angle: {campaign_angle or 'General product promotion'}
Include Hashtags: {include_hashtags}
Include Emoji: {include_emoji}
Call to Action: {call_to_action or 'Encourage engagement'}

Write the complete post. For Instagram, put hashtags after a line break or at the end.
Return ONLY the post text — no labels or explanations."""

    return _sync_llm(system, user)


@tool
def research_poster_inspiration(
    product_analysis: str,
    product_keywords: str = "",
    poster_type: str = "promotional",
    count: Union[int, str] = 6,
) -> str:
    """
    Research poster/template inspiration for a product before generating HTML.

    This tool searches for design references and distills them into a safe,
    original creative brief. References are for moodboard/art-direction only:
    do not copy exact templates, logos, compositions, or third-party images.

    Args:
        product_analysis: JSON string from analyze_product_image, or a concise
            product JSON if the user described the product in text.
        product_keywords: Extra search terms from the user's prompt, e.g.
            "double cheeseburger restaurant offer".
        poster_type: promotional | launch | sale | seasonal | minimal | bold | luxury
        count: Number of references to collect, clamped to 3-8.

    Returns:
        JSON with web references, visual references, and a distilled design brief.
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 6
    count = max(3, min(count, 8))

    product_data = _parse_product_analysis(product_analysis)
    terms = _product_search_terms(product_data, product_keywords)
    signal = _category_signal(product_data, product_keywords)
    core_query = _clean_phrase(" ".join(terms[:7]))[:140] or "product"

    if signal == "food":
        search_queries = [
            f"{core_query} restaurant food poster design template",
            f"{core_query} burger food advertising poster inspiration",
            f"{core_query} social media food promo flyer design",
        ]
    elif signal == "fashion":
        search_queries = [
            f"{core_query} fashion campaign poster design template",
            f"{core_query} editorial product poster inspiration",
            f"{core_query} clothing promotional poster design",
        ]
    elif signal == "tech":
        search_queries = [
            f"{core_query} tech product launch poster design template",
            f"{core_query} electronics advertising poster inspiration",
            f"{core_query} premium gadget poster design",
        ]
    elif signal == "beauty":
        search_queries = [
            f"{core_query} beauty product poster design template",
            f"{core_query} skincare cosmetic advertising poster inspiration",
            f"{core_query} elegant beauty promo poster design",
        ]
    else:
        search_queries = [
            f"{core_query} {poster_type} poster design template",
            f"{core_query} advertising poster inspiration",
            f"{core_query} social media promo design",
        ]

    web_references: list[dict[str, str]] = []
    visual_references: list[dict[str, str]] = []
    warnings: list[str] = []

    for query in search_queries[:2]:
        results, err = _ddgs_text_search(query, max(2, count // 2))
        web_references.extend(results)
        if err:
            warnings.append(err)
            break

    image_results, err = _ddgs_image_search(search_queries[0], count)
    visual_references.extend(image_results)
    if err:
        warnings.append(err)

    # Dedupe references while preserving order.
    deduped_web: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for ref in web_references:
        url = ref.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped_web.append(ref)
        if len(deduped_web) >= count:
            break

    deduped_visuals: list[dict[str, str]] = []
    seen_images: set[str] = set()
    for ref in visual_references:
        url = ref.get("image_url", "")
        if not url or url in seen_images:
            continue
        seen_images.add(url)
        deduped_visuals.append(ref)
        if len(deduped_visuals) >= count:
            break

    payload = {
        "status": "web_researched" if (deduped_web or deduped_visuals) else "fallback_brief",
        "usage_policy": (
            "Use references only as moodboard inspiration. Create an original poster. "
            "Do not copy exact templates, protected logos, or third-party artwork; "
            "do not hotlink reference/template images into the poster."
        ),
        "detected_product": {
            "name": product_data.get("product_name") or product_data.get("name") or "Product",
            "category": product_data.get("category") or signal,
            "subcategory": product_data.get("subcategory") or "",
            "keywords": terms[:10],
        },
        "search_queries": search_queries,
        "web_references": deduped_web,
        "visual_references": deduped_visuals,
        "design_brief": _fallback_design_brief(product_data, poster_type, product_keywords),
        "warnings": _dedupe_keep_order(warnings, limit=3),
    }
    return json.dumps(payload, indent=2)


@tool
def generate_html_poster(
    product_analysis: str,
    poster_type: str = "promotional",
    color_scheme: str = "auto",
    headline: str = "",
    tagline: str = "",
    additional_text: str = "",
    image_url: str = "",
    design_references: str = "",
) -> str:
    """
    Generate a complete, visually stunning HTML/CSS promotional poster.
    The poster is returned as a full HTML string that can be rendered in a browser
    and exported as an image by the frontend.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        poster_type: promotional | launch | sale | seasonal | minimal | bold | luxury
        color_scheme: auto (derived from product) | dark | light | vibrant | monochrome | or hex colors e.g. "#FF0000,#000000"
        headline: Main headline text (LLM generates one if empty)
        tagline: Supporting tagline (LLM generates one if empty)
        additional_text: Any additional copy to include
        image_url: URL of the product image to include in the poster
        design_references: JSON returned by research_poster_inspiration

    Returns:
        Complete HTML/CSS poster as a string (ready for iframe rendering)
    """
    system = """You are a world-class graphic designer producing print-quality product marketing posters in pure HTML + CSS.

OUTPUT FORMAT — absolutely critical:
- Return ONLY the raw HTML document, starting with <!DOCTYPE html> and ending with </html>.
- Do NOT wrap the HTML in markdown code fences (no ```html, no ```).
- Do NOT include any prose, preamble, or explanation before or after the HTML.
- Use only inline <style> — no external stylesheets, no CDN scripts, no JS.
- Google Fonts via @import inside <style> is allowed.

CANVAS
- Fix the poster at exactly 800×1000px. Body has padding 0/margin 0; the poster lives in a single .poster container with those dimensions, overflow:hidden.

PRODUCT IMAGE
- If a product image URL is provided, include it with <img src="{{URL}}" style="..."> as the hero focal point. object-fit:cover and a tasteful framing device (rounded corners, soft shadow, or a colored plate behind it).
- Never leave an empty placeholder box. If no URL is provided, replace that area with a bold typographic composition or an abstract CSS shape — not a blank rectangle.

RESEARCH AND ORIGINALITY
- If design references are provided, treat them as a moodboard and strategy brief only.
- Extract broad patterns: layout energy, color mood, typography style, CTA placement, offer badge style, visual hierarchy.
- Do NOT copy any exact template, third-party artwork, logo, watermark, or composition.
- Do NOT hotlink design-reference/template images into the poster. The only <img> should be the product image URL provided by the user/tool.
- The final poster must be an original design that merely learns from the references.

DESIGN DIRECTION — pick ONE and commit to it, based on poster_type and the product's brand aesthetic:
- minimalist/luxury: generous whitespace, 1-2 fonts max (e.g., Playfair Display + Inter), tight tracking on serif headline, muted neutrals (off-white, warm stone, graphite). Avoid bright saturated buttons — use a thin outlined CTA.
- bold/streetwear: heavy display type (Anton, Bebex, Space Grotesk 900), off-grid composition, one accent color at high saturation, deliberate overlap between image and text.
- editorial: magazine-style pull quote, asymmetric grid, small caps labels, horizontal rule dividers.
- vibrant/lifestyle: gradient backgrounds, playful geometric shapes behind the product, rounded chunky CTA.

FOOD / BURGER / RESTAURANT QUALITY BAR
- If the product is food, a burger, a restaurant item, or a beverage, do NOT use the quiet fashion/minimal template by default.
- Build an appetite-first ad: oversized close-up hero, warm glow, textured/dark backdrop, ketchup-red or mustard-gold accent, price/offer badge, ingredient chips, and a clear ordering CTA.
- Use CSS-only decorative elements that feel like sauce swashes, sesame dots, heat/glow rings, menu stickers, torn-paper coupons, or dynamic diagonal panels.
- The food/product must feel large, juicy, premium, and immediately craveable.

TYPOGRAPHY
- Clear hierarchy: eyebrow label (10-12px, tracked out, uppercase), headline (64-96px, tight leading), tagline (18-22px), body/description (14-16px), CTA (14-16px uppercase with tracking).
- Maximum 2 font families. Mix weights within a family instead of adding fonts.

COLOR
- Derive palette from the product's actual colors. For a white/cream garment with a "quiet luxury" aesthetic, use neutrals (#FAFAF7, #E8E4DD, #1A1A1A, warm accent) — do NOT default to bright cobalt blue CTA buttons; that clashes with minimalist products.
- Max 3 colors + 1 accent.

POLISH
- Real CSS shadows, subtle gradients, hairline borders (1px solid rgba), proper optical spacing. Avoid generic "AI-looking" centered-everything layouts unless the brief is explicitly minimalist.
- Pull real product details from the analysis into the copy (name, materials, key features) — do not invent unrelated details.

REFERENCE STRUCTURE (adapt to the chosen direction, do not copy verbatim):
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Inter:wght@400;500;700&display=swap');
  body{margin:0;font-family:'Inter',sans-serif;background:#EAE6DF;}
  .poster{position:relative;width:800px;height:1000px;background:#FAFAF7;overflow:hidden;padding:56px;box-sizing:border-box;display:flex;flex-direction:column;}
  .eyebrow{font-size:11px;letter-spacing:.24em;text-transform:uppercase;color:#6E6A60;margin-bottom:8px;}
  .headline{font-family:'Playfair Display',serif;font-size:78px;line-height:.95;color:#17150F;margin:0 0 16px;letter-spacing:-.01em;}
  .tagline{font-size:20px;line-height:1.45;color:#3A3731;max-width:540px;margin:0;}
  .hero{flex:1;margin:32px -16px 24px;display:flex;align-items:center;justify-content:center;background:#EDE8DF;border-radius:18px;overflow:hidden;}
  .hero img{width:100%;height:100%;object-fit:cover;}
  .footer{display:flex;justify-content:space-between;align-items:center;border-top:1px solid rgba(23,21,15,.12);padding-top:18px;}
  .meta{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#6E6A60;}
  .cta{font-size:13px;letter-spacing:.22em;text-transform:uppercase;color:#17150F;border:1px solid #17150F;padding:14px 26px;border-radius:999px;}
</style></head><body>
  <div class="poster">
    <div class="eyebrow">New Arrival · SS26</div>
    <h1 class="headline">The Oversized<br/>White Tee</h1>
    <p class="tagline">Heavyweight cotton. A relaxed fit cut to move with you.</p>
    <div class="hero"><img src="{{IMAGE_URL}}" alt="Product"/></div>
    <div class="footer"><span class="meta">Studio Everyday</span><span class="cta">Shop The Drop</span></div>
  </div>
</body></html>

Adapt this structure: substitute real product name, real copy from the analysis, and choose a palette/typography system matching the chosen direction. If the direction is bold/streetwear, replace Playfair+Inter with a heavy display font (Anton/Space Grotesk), use stronger contrast and off-grid layout. If editorial, use columns and a pull quote. Never leave {{IMAGE_URL}} as a literal — either substitute the provided image URL or replace the hero with a typographic composition."""

    product_data = _parse_product_analysis(product_analysis)

    image_instruction = (
        f"Use this exact URL in the <img> tag: {image_url}"
        if image_url
        else "No product image URL was provided — build a strong typographic/abstract hero instead. Do NOT leave a blank placeholder."
    )

    user = f"""Create a stunning {poster_type} marketing poster for this product.

Product Details:
{json.dumps(product_data, indent=2)}

Poster Type: {poster_type}
Color Scheme: {color_scheme} — derive from the product's actual colors and price tier if 'auto'
Headline: {headline or 'Generate a compelling headline (4-8 words, specific to this product)'}
Tagline: {tagline or 'Generate a powerful tagline (1 short line)'}
Additional Text: {additional_text or 'Include a 1-2 sentence description that uses real product attributes'}
Product Image: {image_instruction}

Design / Template Inspiration:
{design_references or 'No external design research was provided. Use product category best practices and create an original premium poster.'}

How to use inspiration:
- If references are present, use their patterns only as art direction.
- Never copy a template exactly and never include reference/template images.
- Prefer the product image URL above as the only real image asset.
- If the product is a burger/food item, prioritize appetite appeal, scale, warm contrast, and offer-badge energy over minimalist whitespace.

Design principles to follow:
1. Professional typographic hierarchy
2. Ample white/negative space
3. Visual focal point on the product
4. Clear call-to-action area
5. Brand-appropriate color usage

Return the complete HTML document only."""

    raw = _sync_llm(system, user)
    cleaned = _strip_code_fence(raw)
    return _ensure_image_in_poster(cleaned, image_url)


@tool
def edit_html_poster(
    previous_html: str,
    edit_instructions: str,
    image_url: str = "",
) -> str:
    """
    Apply surgical edits to an EXISTING poster's HTML/CSS while preserving
    everything the user did not ask to change.

    Use this (not generate_html_poster) whenever the user asks to tweak a
    poster that was already generated — e.g. "make the background pink",
    "change the headline to X", "make the font serif", "swap the CTA colour".

    Args:
        previous_html: The full HTML document of the current poster (injected
            automatically via [LATEST POSTER HTML] in system context).
        edit_instructions: Plain-language description of what the user wants
            changed. Be faithful to their wording.
        image_url: Optional — only pass if a NEW product image URL should
            replace the existing one.

    Returns:
        Complete updated HTML document (same 800×1000 canvas, same overall
        structure), with only the requested changes applied.
    """
    system = """You are a senior front-end designer performing a targeted edit on an existing marketing poster.

OUTPUT FORMAT:
- Return ONLY the full updated HTML document, starting with <!DOCTYPE html> and ending with </html>.
- No markdown code fences, no prose, no "here's the update", no before/after diff — just the new HTML.

EDIT DISCIPLINE:
- Change ONLY what the user asked to change. Keep every other element (layout, fonts, copy, image, other colors, spacing) pixel-identical.
- If the user says "make the background pink", change only the background color rule(s) — do not re-write the rest of the poster.
- Preserve the 800×1000 canvas dimensions.
- Preserve the existing <img> src unless a new image_url is given.
- Never introduce placeholders or "lorem ipsum". Keep all real copy.
"""

    image_note = (
        f"A new product image URL is provided — replace the existing <img src> with: {image_url}"
        if image_url
        else "No new image was provided — keep the existing <img src> exactly as it is."
    )

    user = f"""Here is the current poster HTML:

----- CURRENT POSTER (begin) -----
{previous_html}
----- CURRENT POSTER (end) -----

User's edit request: {edit_instructions}

{image_note}

Return the full updated HTML document now."""

    raw = _sync_llm(system, user)
    cleaned = _strip_code_fence(raw)
    # Preserve the image: if the edit dropped the img tag, put it back.
    if image_url:
        return _ensure_image_in_poster(cleaned, image_url)
    # No new image provided — try to carry forward whatever was in the previous HTML.
    prev_src_match = re.search(
        r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
        previous_html or "",
        flags=re.IGNORECASE,
    )
    if prev_src_match:
        return _ensure_image_in_poster(cleaned, prev_src_match.group(1))
    return cleaned


def _strip_code_fence(text: str) -> str:
    """Remove a leading ```html / ``` fence and trailing ``` that LLMs often wrap HTML in."""
    s = text.strip()
    if s.startswith("```"):
        # drop the first fence line (```html, ```HTML, ```, etc.)
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        else:
            s = s[3:]
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3]
    return s.strip()


_PLACEHOLDER_SRC_PATTERNS = (
    "{{image_url}}", "{{ image_url }}", "{image_url}",
    "placeholder", "your-image-url", "product.jpg", "image.jpg",
    "example.com", "via.placeholder",
)


def _ensure_image_in_poster(html: str, image_url: str) -> str:
    """If we know the product image URL, guarantee it's actually in the poster.

    Handles three LLM failure modes:
      1. Left a literal {{IMAGE_URL}} / placeholder in src.
      2. Wrote a generic/empty src.
      3. Skipped the <img> tag entirely.
    """
    if not image_url or not html:
        return html

    # 1. Substitute any literal placeholder token (case-insensitive).
    def _sub_placeholder(m: "re.Match[str]") -> str:
        attr_full = m.group(0)
        inner_lower = m.group(1).lower()
        if any(p in inner_lower for p in _PLACEHOLDER_SRC_PATTERNS):
            quote = attr_full[4]
            return f'src={quote}{image_url}{quote}'
        return attr_full

    html = re.sub(r'src=(["\'])([^"\']*)\1', _sub_placeholder, html, flags=re.IGNORECASE)

    # 2. If the URL is already present somewhere, we're done.
    if image_url in html:
        return html

    # 3. If there's at least one <img> tag, point its src at the real URL.
    img_match = re.search(r'<img\b([^>]*)>', html, flags=re.IGNORECASE)
    if img_match:
        attrs = img_match.group(1)
        # Replace existing src or add one.
        if re.search(r'\bsrc\s*=', attrs, flags=re.IGNORECASE):
            new_attrs = re.sub(
                r'\bsrc\s*=\s*(["\'])[^"\']*\1',
                f'src="{image_url}"',
                attrs,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            new_attrs = f' src="{image_url}"' + attrs
        return html[: img_match.start()] + f'<img{new_attrs}>' + html[img_match.end() :]

    # 4. No <img> at all — inject a hero image block right inside the first
    # element that looks like the poster container (.poster / .hero / body).
    hero_html = (
        f'<div style="width:100%;aspect-ratio:4/5;background:#EDE8DF;border-radius:18px;'
        f'overflow:hidden;margin:32px 0;">'
        f'<img src="{image_url}" alt="Product" style="width:100%;height:100%;object-fit:cover;"/>'
        f'</div>'
    )
    # Inject after the opening tag of the first obvious container.
    anchor = re.search(
        r'(<(?:div|section|main)\b[^>]*class=["\'][^"\']*(?:poster|hero|container)[^"\']*["\'][^>]*>)',
        html,
        flags=re.IGNORECASE,
    )
    if anchor:
        return html[: anchor.end()] + hero_html + html[anchor.end() :]
    # Last resort: insert right after <body ...>.
    body_match = re.search(r'<body\b[^>]*>', html, flags=re.IGNORECASE)
    if body_match:
        return html[: body_match.end()] + hero_html + html[body_match.end() :]
    return html + hero_html


@tool
def generate_hashtags(
    product_analysis: str,
    platform: str = "instagram",
    count: int = 30,
    include_trending: Union[bool, str] = True,
    niche: str = "",
) -> str:
    """
    Generate a strategic set of hashtags for social media posts.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        platform: instagram | twitter | linkedin | tiktok
        count: Number of hashtags to generate
        include_trending: Include potentially trending/broad hashtags
        niche: Specific niche to target e.g., "streetwear", "sustainable fashion"

    Returns:
        JSON object with hashtags categorised by type:
        {mega, large, medium, small, niche, branded_suggestion}
    """
    system = """You are a social media hashtag strategist.
Generate hashtags across multiple reach tiers for maximum discoverability.
Return ONLY a JSON object — no preamble."""

    user = f"""Product Analysis:
{product_analysis}

Platform: {platform}
Total Count: {count}
Include Trending: {_as_bool(include_trending, default=True)}
Niche Focus: {niche or 'auto-detect from product'}

Return a JSON object:
{{
  "mega": ["hashtags with 10M+ posts — broad reach"],
  "large": ["hashtags with 1M-10M posts"],
  "medium": ["hashtags with 100K-1M posts — sweet spot"],
  "small": ["hashtags with 10K-100K posts — highly targeted"],
  "niche": ["very specific hashtags under 10K posts"],
  "branded_suggestion": ["suggested brand-specific hashtag"],
  "ready_to_use": "all hashtags formatted in one block ready to copy"
}}

Return ONLY the raw JSON object. Do NOT wrap it in ```json code fences or any other markdown."""

    result = _sync_llm(system, user)
    return _strip_code_fence(result)


@tool
async def get_product_context(session_id: str) -> str:
    """
    Retrieve the stored product analysis and context for this session from the database.
    Use this at the start of a conversation if no image was provided but the user
    is asking about a product that was analysed in a previous message.

    Args:
        session_id: The current chat session ID

    Returns:
        JSON string with product analysis, or a message indicating no product is stored
    """
    from services.redis_service import get_cached_product_analysis
    from services.mongodb_service import get_product

    cached = await get_cached_product_analysis(session_id)
    if cached:
        return cached

    product = await get_product(session_id)
    if product:
        return json.dumps({
            "product_name": product.get("name", "Unknown"),
            "analysis": product.get("analysis", ""),
            "image_url": product.get("image_url", ""),
        })

    return json.dumps({"message": "No product has been analysed in this session yet. Please upload a product image first."})


@tool
async def save_generated_content(
    session_id: str,
    content_type: str,
    content: str,
    platform: str = "",
    metadata: str = "{}",
) -> str:
    """
    Save generated marketing content to the database for later retrieval.
    Always call this after generating any content to persist it.

    Args:
        session_id: The current chat session ID
        content_type: description | poster | social_post | hashtags | campaign | ad_copy
        content: The generated content to save
        platform: Optional platform name for social posts
        metadata: Optional JSON string with additional metadata

    Returns:
        Confirmation message with the saved content ID
    """
    from services.mongodb_service import save_content
    from models.schemas import GeneratedContentDocument

    ct_map = {
        "description": "description", "poster": "poster",
        "social_post": "social_post", "hashtags": "hashtags",
        "campaign": "campaign", "ad_copy": "ad_copy",
    }
    content_type_norm = ct_map.get(content_type.lower(), "description")

    meta = {}
    try:
        meta = json.loads(metadata)
    except Exception:
        pass

    doc = GeneratedContentDocument(
        session_id=session_id,
        content_type=content_type_norm,
        content=content,
        platform=platform if platform else None,
        metadata=meta,
    )
    content_id = await save_content(doc)
    return json.dumps({"success": True, "id": content_id, "message": f"Content saved with ID: {content_id}"})


@tool
def generate_campaign_brief(
    product_analysis: str,
    campaign_goal: str,
    budget_tier: str = "medium",
    duration: str = "1 month",
    platforms: str = "instagram,linkedin",
) -> str:
    """
    Generate a complete multi-platform marketing campaign brief.

    Args:
        product_analysis: JSON string from analyze_product_image tool
        campaign_goal: e.g., "product launch", "brand awareness", "drive sales", "grow followers"
        budget_tier: low | medium | high | enterprise
        duration: Campaign duration e.g., "2 weeks", "1 month", "Q4"
        platforms: Comma-separated platforms to include

    Returns:
        Complete marketing campaign brief with strategy, content calendar, and messaging
    """
    system = """You are a senior marketing strategist at a top creative agency.
Create comprehensive campaign briefs that are strategic, actionable, and results-oriented.
Format your response in clean Markdown."""

    user = f"""Product Analysis:
{product_analysis}

Campaign Goal: {campaign_goal}
Budget Tier: {budget_tier}
Duration: {duration}
Platforms: {platforms}

Create a complete campaign brief including:
1. **Campaign Concept & Theme** — Big idea, creative direction
2. **Target Audience** — Detailed persona, psychographics
3. **Key Messages** — Core messaging hierarchy
4. **Platform Strategy** — Tailored approach per platform
5. **Content Calendar** — Weekly posting schedule with content types
6. **Content Themes** — 5-7 content pillars
7. **KPIs & Success Metrics** — What to measure
8. **Hashtag Strategy** — Platform-specific hashtag groups
9. **Tone & Voice Guidelines** — How to communicate
10. **Sample Post Hooks** — 5 proven opening lines for posts"""

    return _sync_llm(system, user)


# ─── Product image finder (web fallback) ──────────────────────────────────────

@tool
def find_product_image(query: str, count: Union[int, str] = 3) -> str:
    """
    Search the web for a product photo when the user hasn't uploaded one.

    Use this when:
      - The user asks for a poster/description for a product BUT there is no
        [PRODUCT IMAGE URL] in system context.
      - You need a visual to feed into generate_html_poster.image_url.

    Args:
        query: Short descriptive search phrase — what the product is.
               Good: "minimalist white ceramic coffee mug"
               Bad:  "mug"  ← too generic
        count: How many candidate URLs to return (1-5).

    Returns:
        A JSON object with:
          { "query": "...", "images": [ { "url": "...", "title": "..." }, ... ],
            "primary_url": "<first good URL to use as image_url>" }
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(count, 5))
    DDGS, import_error = _get_ddgs_class()
    if not DDGS:
        return json.dumps({
            "error": import_error,
            "query": query,
            "images": [],
            "primary_url": "",
        })

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.images(
                query,
                region="wt-wt",
                safesearch="moderate",
                size="Large",
                max_results=count * 2,  # fetch extra, filter below
            ):
                url = r.get("image") or r.get("url") or ""
                title = r.get("title") or ""
                if not url.lower().startswith(("http://", "https://")):
                    continue
                if any(url.lower().endswith(ext) for ext in (".svg",)):
                    continue
                results.append({"url": url, "title": title})
                if len(results) >= count:
                    break
    except Exception as e:
        logger.error(f"find_product_image failed: {e}")
        return json.dumps({
            "error": f"Image search failed: {e}",
            "query": query,
            "images": [],
            "primary_url": "",
        })

    return json.dumps({
        "query": query,
        "images": results,
        "primary_url": results[0]["url"] if results else "",
    })


# ─── Clarifying-questions tool ────────────────────────────────────────────────

_CLARIFICATION_QUESTIONS = {
    "poster": {
        "intro": "Pick a few options and I'll design a poster that matches your vibe:",
        "questions": [
            {
                "id": "direction",
                "label": "Design direction",
                "type": "single",
                "options": [
                    "Minimalist / Luxury",
                    "Bold / Streetwear",
                    "Editorial magazine-style",
                    "Vibrant / Lifestyle",
                    "Retro / Vintage",
                ],
            },
            {
                "id": "palette",
                "label": "Color palette",
                "type": "single",
                "options": [
                    "Neutral (off-white, stone, graphite)",
                    "Monochrome (black & white)",
                    "Warm earthy tones",
                    "Cool blues and greens",
                    "Bold accent pop",
                ],
            },
            {
                "id": "audience",
                "label": "Target audience",
                "type": "single",
                "options": [
                    "Teens (13–19)",
                    "Young adults (20–29)",
                    "Millennials (30–40)",
                    "Gen X / 40+",
                    "All ages",
                ],
            },
            {
                "id": "angle",
                "label": "Campaign angle (pick any)",
                "type": "multi",
                "options": [
                    "New arrival",
                    "Seasonal drop",
                    "Limited edition",
                    "Sale / promotion",
                    "Evergreen brand",
                ],
            },
            {
                "id": "cta",
                "label": "Call to action",
                "type": "single",
                "options": [
                    "Shop now",
                    "Pre-order",
                    "Discover the drop",
                    "Learn more",
                    "Subscribe",
                ],
            },
        ],
    },
    "description": {
        "intro": "A few quick choices so the description lands right:",
        "questions": [
            {
                "id": "tone",
                "label": "Tone of voice",
                "type": "single",
                "options": [
                    "Luxury / sophisticated",
                    "Casual / friendly",
                    "Playful / witty",
                    "Minimalist / product-focused",
                    "Inspirational / aspirational",
                ],
            },
            {
                "id": "length",
                "label": "Length",
                "type": "single",
                "options": [
                    "Short (~50 words)",
                    "Medium (~120 words)",
                    "Long (~200 words)",
                ],
            },
            {
                "id": "focus",
                "label": "What should the copy emphasise? (pick any)",
                "type": "multi",
                "options": [
                    "Materials / craftsmanship",
                    "Comfort / fit",
                    "Style / aesthetic",
                    "Sustainability",
                    "Value / price",
                    "Versatility / how to wear",
                ],
            },
            {
                "id": "audience",
                "label": "Target audience",
                "type": "single",
                "options": [
                    "Teens (13–19)",
                    "Young adults (20–29)",
                    "Millennials (30–40)",
                    "Gen X / 40+",
                    "All ages",
                ],
            },
            {
                "id": "channel",
                "label": "Where will this be used?",
                "type": "single",
                "options": [
                    "Product page / e-commerce",
                    "Marketplace listing (Amazon/Etsy)",
                    "Social media caption",
                    "Email newsletter",
                    "Print catalogue",
                ],
            },
        ],
    },
}


@tool
def ask_clarifying_questions(content_type: str = "poster") -> str:
    """
    Present 4–5 multi-choice questions to the user BEFORE generating a poster
    or a product description. The frontend renders the returned JSON as an
    interactive card with checkboxes/radios; the user's selections come back
    as their next message.

    Call this at the START of a poster or description request when the user
    hasn't already specified strong preferences. After calling this tool:
      1. Reply with a single short sentence ("Pick what fits and I'll get designing.")
      2. Do NOT call any other tool in this turn.
      3. Wait for the user's next message (which will contain their picks).

    Do NOT call this tool again for the same content type in the same session
    — one set of questions per piece of content. Do NOT call it for edits to
    an existing poster (use edit_html_poster instead).

    Args:
        content_type: "poster" or "description"

    Returns:
        A JSON string with keys: intro, content_type_target, questions
    """
    key = content_type.lower().strip()
    if key not in _CLARIFICATION_QUESTIONS:
        key = "poster"
    payload = dict(_CLARIFICATION_QUESTIONS[key])
    payload["content_type_target"] = key
    return json.dumps(payload)


# ── Tool registry — passed to the agent ───────────────────────────────────────

ALL_TOOLS = [
    ask_clarifying_questions,
    analyze_product_image,
    find_product_image,
    research_poster_inspiration,
    generate_marketing_copy,
    generate_social_media_post,
    generate_html_poster,
    edit_html_poster,
    generate_hashtags,
    get_product_context,
    save_generated_content,
    generate_campaign_brief,
]
