from __future__ import annotations

import os
from typing import Optional

import serpapi
from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class FlightsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, coerce_numbers_to_str=True)

    departure_airport: Optional[str] = Field(
        default=None,
        description="Departure airport code (IATA).",
        validation_alias=AliasChoices("departure_airport", "departure", "from_airport", "origin"),
    )
    arrival_airport: Optional[str] = Field(
        default=None,
        description="Arrival airport code (IATA).",
        validation_alias=AliasChoices("arrival_airport", "arrival", "to_airport", "destination"),
    )
    outbound_date: Optional[str] = Field(
        default=None,
        description="Outbound date in YYYY-MM-DD format.",
        validation_alias=AliasChoices("outbound_date", "departure_date", "depart_date", "start_date"),
    )
    return_date: Optional[str] = Field(
        default=None,
        description="Return date in YYYY-MM-DD format.",
        validation_alias=AliasChoices("return_date", "inbound_date", "end_date"),
    )
    adults: Optional[int] = Field(default=1, description="Number of adults.")
    children: Optional[int] = Field(default=0, description="Number of children.")
    infants_in_seat: Optional[int] = Field(default=0, description="Number of infants in seats.")
    infants_on_lap: Optional[int] = Field(default=0, description="Number of infants on lap.")


class FlightsInputSchema(BaseModel):
    params: FlightsInput


@tool(args_schema=FlightsInputSchema)
def flights_finder(params: FlightsInput):
    """Find flights using SerpApi Google Flights engine."""

    api_key = (os.environ.get("SERPAPI_API_KEY") or "").strip()
    if not api_key:
        return {"error": "SERPAPI_API_KEY is not configured."}

    payload = {
        "api_key": api_key,
        "engine": "google_flights",
        "hl": "en",
        "gl": "us",
        "departure_id": params.departure_airport,
        "arrival_id": params.arrival_airport,
        "outbound_date": params.outbound_date,
        "return_date": params.return_date,
        "currency": "INR",
        "adults": params.adults,
        "children": params.children,
        "infants_in_seat": params.infants_in_seat,
        "infants_on_lap": params.infants_on_lap,
        "stops": "1",
    }

    try:
        search = serpapi.search(payload)
        data = getattr(search, "data", {}) or {}
        results = data.get("best_flights", []) or data.get("other_flights", [])
        if not isinstance(results, list):
            results = []

        dep = payload.get("departure_id") or ""
        arr = payload.get("arrival_id") or ""
        out_d = payload.get("outbound_date") or ""
        ret_d = payload.get("return_date") or ""
        google_flights_url = (
            f"https://www.google.com/travel/flights?q=Flights+from+{dep}+to+{arr}"
            f"+on+{out_d}" + (f"+returning+{ret_d}" if ret_d else "")
        )

        for flight in results:
            if not isinstance(flight, dict):
                continue
            flight["booking_link"] = google_flights_url
            flight["skyscanner_link"] = (
                f"https://www.skyscanner.co.in/transport/flights/{dep}/{arr}/"
                f"{out_d.replace('-', '')[2:]}" + (f"/{ret_d.replace('-', '')[2:]}" if ret_d else "")
            )
            flight["kayak_link"] = (
                f"https://www.kayak.co.in/flights/{dep}-{arr}/{out_d}" + (f"/{ret_d}" if ret_d else "")
            )
            flight["makemytrip_link"] = (
                f"https://www.makemytrip.com/flight/search?itinerary={dep}-{arr}-{('-'.join(reversed(out_d.split('-'))))}"
                + (f"_{arr}-{dep}-{('-'.join(reversed(ret_d.split('-'))))}" if ret_d else "")
                + "&tripType="
                + ("R" if ret_d else "O")
                + "&paxType=A-1_C-0_I-0&intl=false&cabinClass=E"
            )

        return results[:10]
    except Exception as exc:
        return {"error": str(exc)}
