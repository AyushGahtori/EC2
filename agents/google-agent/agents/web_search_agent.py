"""
Web Search Agent
Real-time web search using DuckDuckGo
"""

import logging
import re
from html import unescape
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class WebSearchAgent(BaseAgent):
    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='search_query: the optimal search query to find information',
            example_output='{"search_query": "latest AI news 2026"}',
            context=context,
        )
        query = params.get("search_query", user_message)

        results = await self._search_duckduckgo(query)
        if not results:
            return self.success(
                summary=f"[Web Search] No results found for: '{query}'. Try rephrasing your query.",
                data={"query": query, "results": []},
            )

        summary = await self._summarize_results(user_message, query, results)
        return self.success(summary=summary, data={"query": query, "results": results[:5]})

    async def _search_duckduckgo(self, query: str) -> List[Dict[str, str]]:
        """Search DuckDuckGo using instant answers first, then HTML fallback."""
        instant_results = await self._search_duckduckgo_instant(query)
        if instant_results:
            return instant_results
        return await self._search_duckduckgo_html(query)

    async def _search_duckduckgo_instant(self, query: str) -> List[Dict[str, str]]:
        """Search using DuckDuckGo instant answers API."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": query,
                        "format": "json",
                        "no_redirect": 1,
                        "no_html": 1,
                        "skip_disambig": 1,
                    },
                )
                if response.status_code != 200:
                    return []

                data = response.json()
                results = []

                if data.get("AbstractText"):
                    results.append(
                        {
                            "title": data.get("Heading", "Answer"),
                            "snippet": data["AbstractText"],
                            "url": data.get("AbstractURL", ""),
                        }
                    )

                for topic in data.get("RelatedTopics", [])[:4]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(
                            {
                                "title": topic.get("Text", "")[:80],
                                "snippet": topic.get("Text", ""),
                                "url": topic.get("FirstURL", ""),
                            }
                        )

                return results
        except Exception as exc:
            logger.error(f"DuckDuckGo instant search error: {exc}")

        return []

    async def _search_duckduckgo_html(self, query: str) -> List[Dict[str, str]]:
        """Fallback HTML search that returns standard web results."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={
                        "User-Agent": "Mozilla/5.0",
                    },
                )
                if response.status_code != 200:
                    return []

                html = response.text
                results = []
                result_pattern = re.compile(
                    r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?(?:<a[^>]*class="result__snippet"[^>]*>|<div[^>]*class="result__snippet"[^>]*>)(?P<snippet>.*?)(?:</a>|</div>)',
                    re.IGNORECASE | re.DOTALL,
                )

                for match in result_pattern.finditer(html):
                    title = self._clean_html(match.group("title"))
                    snippet = self._clean_html(match.group("snippet"))
                    url = self._normalize_result_url(unescape(match.group("url")))
                    if title and snippet:
                        results.append({"title": title, "snippet": snippet, "url": url})
                    if len(results) >= 5:
                        break

                return results
        except Exception as exc:
            logger.error(f"DuckDuckGo HTML search error: {exc}")

        return []

    async def _summarize_results(self, user_message: str, query: str, results: List[Dict[str, str]]) -> str:
        """Summarize search results with the configured LLM."""
        results_text = "\n".join(
            f"- {result['title']}: {result['snippet'][:200]} ({result['url']})"
            for result in results[:5]
        )

        response = await self.llm_complete(
            messages=[
                {
                    "role": "user",
                    "content": f"""User asked: "{user_message}"

Search query: "{query}"
Search results:
{results_text}

Provide a clear, concise answer based on these search results. Include key facts and source URLs where relevant.""",
                }
            ],
            system_prompt="You are a helpful assistant summarizing web search results. Be accurate and cite sources.",
        )
        return f"Web Search Results for '{query}':\n\n{response}"

    def _clean_html(self, text: str) -> str:
        stripped = re.sub(r"<[^>]+>", "", text or "")
        return unescape(stripped).replace("\n", " ").strip()

    def _normalize_result_url(self, url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url

        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
            target_url = parse_qs(parsed.query).get("uddg", [])
            if target_url:
                return unquote(target_url[0])

        return url
