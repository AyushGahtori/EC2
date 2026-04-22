from __future__ import annotations

import os
from typing import Optional

import serpapi
from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class HotelsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, coerce_numbers_to_str=True)

    q: str = Field(
        description="Hotel location.",
        validation_alias=AliasChoices("q", "location", "destination", "city"),
    )
    check_in_date: str = Field(
        description="Check-in date in YYYY-MM-DD format.",
        validation_alias=AliasChoices("check_in_date", "check_in", "checkin_date", "checkin"),
    )
    check_out_date: str = Field(
        description="Check-out date in YYYY-MM-DD format.",
        validation_alias=AliasChoices("check_out_date", "check_out", "checkout_date", "checkout"),
    )
    sort_by: Optional[str] = Field(default="8", description="Sort mode for result ranking.")
    adults: Optional[int] = Field(default=1, description="Number of adults.")
    children: Optional[int] = Field(default=0, description="Number of children.")
    rooms: Optional[int] = Field(default=1, description="Number of rooms.")
    hotel_class: Optional[str] = Field(
        default=None,
        description="Hotel class filter like 2,3,4,5.",
        validation_alias=AliasChoices("hotel_class", "star_rating", "stars", "rating"),
    )


class HotelsInputSchema(BaseModel):
    params: HotelsInput


@tool(args_schema=HotelsInputSchema)
def hotels_finder(params: HotelsInput):
    """Find hotels using SerpApi Google Hotels engine."""

    api_key = (os.environ.get("SERPAPI_API_KEY") or "").strip()
    if not api_key:
        return {"error": "SERPAPI_API_KEY is not configured."}

    payload = {
        "api_key": api_key,
        "engine": "google_hotels",
        "hl": "en",
        "gl": "us",
        "q": params.q,
        "check_in_date": params.check_in_date,
        "check_out_date": params.check_out_date,
        "currency": "INR",
        "adults": params.adults,
        "children": params.children,
        "rooms": params.rooms,
        "sort_by": params.sort_by,
        "hotel_class": params.hotel_class,
    }

    try:
        search = serpapi.search(payload)
        data = getattr(search, "data", {}) or {}
        properties = data.get("properties", [])
        if isinstance(properties, list):
            return properties[:5]
        return []
    except Exception as exc:
        return {"error": str(exc)}
