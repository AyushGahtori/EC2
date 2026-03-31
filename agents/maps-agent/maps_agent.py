import os
from typing import Any

import requests

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
GOOGLE_MAPS_BASE_URL = "https://maps.googleapis.com/maps/api"


class MapsAgentError(Exception):
    pass


class MapsAgent:
    def __init__(self):
        if not GOOGLE_MAPS_API_KEY:
            raise MapsAgentError("GOOGLE_MAPS_API_KEY is not configured.")

    def get_directions(self, origin: str, destination: str, mode: str = "driving") -> dict[str, Any]:
        response = requests.get(
            f"{GOOGLE_MAPS_BASE_URL}/directions/json",
            params={
                "origin": origin,
                "destination": destination,
                "mode": mode,
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=30,
        )
        payload = response.json()
        self._raise_if_bad_status(payload)

        route = payload.get("routes", [{}])[0]
        leg = route.get("legs", [{}])[0]
        steps = [
            {
                "instruction": self._strip_html(step.get("html_instructions", "")),
                "distance": step.get("distance", {}).get("text", ""),
                "duration": step.get("duration", {}).get("text", ""),
            }
            for step in leg.get("steps", [])
        ]
        return {
            "summary": route.get("summary", ""),
            "distance": leg.get("distance", {}).get("text", ""),
            "duration": leg.get("duration", {}).get("text", ""),
            "start_address": leg.get("start_address", ""),
            "end_address": leg.get("end_address", ""),
            "steps": steps,
        }

    def search_places(
        self,
        query: str,
        location: str | None = None,
        radius: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "key": GOOGLE_MAPS_API_KEY}
        if location:
            params["location"] = location
        if radius:
            params["radius"] = radius

        response = requests.get(
            f"{GOOGLE_MAPS_BASE_URL}/place/textsearch/json",
            params=params,
            timeout=30,
        )
        payload = response.json()
        self._raise_if_bad_status(payload)
        return {
            "results": [
                {
                    "name": place.get("name", ""),
                    "address": place.get("formatted_address", ""),
                    "rating": place.get("rating"),
                    "open_now": place.get("opening_hours", {}).get("open_now"),
                    "location": place.get("geometry", {}).get("location"),
                }
                for place in payload.get("results", [])[:10]
            ]
        }

    def geocode(self, address: str | None = None, latlng: str | None = None) -> dict[str, Any]:
        response = requests.get(
            f"{GOOGLE_MAPS_BASE_URL}/geocode/json",
            params={
                "address": address,
                "latlng": latlng,
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=30,
        )
        payload = response.json()
        self._raise_if_bad_status(payload)
        result = payload.get("results", [{}])[0]
        return {
            "formatted_address": result.get("formatted_address", ""),
            "location": result.get("geometry", {}).get("location", {}),
            "place_id": result.get("place_id", ""),
        }

    def distance_matrix(self, origins: str, destinations: str, mode: str = "driving") -> dict[str, Any]:
        response = requests.get(
            f"{GOOGLE_MAPS_BASE_URL}/distancematrix/json",
            params={
                "origins": origins,
                "destinations": destinations,
                "mode": mode,
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=30,
        )
        payload = response.json()
        self._raise_if_bad_status(payload)
        return {
            "origin_addresses": payload.get("origin_addresses", []),
            "destination_addresses": payload.get("destination_addresses", []),
            "rows": payload.get("rows", []),
        }

    def _raise_if_bad_status(self, payload: dict[str, Any]) -> None:
        status = payload.get("status", "UNKNOWN_ERROR")
        if status in {"OK", "ZERO_RESULTS"}:
            return
        raise MapsAgentError(f"Google Maps API error: {status}")

    def _strip_html(self, value: str) -> str:
        return value.replace("<b>", "").replace("</b>", "").replace("<div style=\"font-size:0.9em\">", " ").replace("</div>", " ")
