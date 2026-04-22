import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

CRITICAL_KEYWORDS = [
    "heart attack",
    "cardiac arrest",
    "not breathing",
    "no pulse",
    "unconscious",
    "unresponsive",
    "choking",
    "stroke",
    "seizure",
    "severe bleeding",
    "bleeding heavily",
    "haemorrhage",
    "hemorrhage",
    "anaphylaxis",
    "allergic shock",
    "overdose",
    "poisoning",
    "drowning",
    "electrocution",
    "gunshot",
    "stabbed",
    "stab wound",
    "head injury",
    "skull fracture",
    "spinal injury",
]

HIGH_KEYWORDS = [
    "chest pain",
    "difficulty breathing",
    "shortness of breath",
    "severe pain",
    "broken bone",
    "fracture",
    "dislocation",
    "deep cut",
    "burn",
    "scalded",
    "high fever",
    "diabetic",
    "asthma attack",
    "panic attack",
    "severe headache",
    "vomiting blood",
    "blood in urine",
    "fainting",
    "fainted",
    "pregnancy",
    "labour",
    "miscarriage",
    "baby",
]

MEDIUM_KEYWORDS = [
    "injured",
    "accident",
    "fell",
    "fall",
    "hit",
    "bleeding",
    "pain",
    "hurt",
    "wound",
    "cut",
    "bruise",
    "dizzy",
    "nausea",
    "vomiting",
    "fever",
    "swelling",
    "allergic",
    "infection",
    "bite",
    "sting",
    "sprain",
    "twisted",
]

LOW_KEYWORDS = [
    "minor",
    "small",
    "slight",
    "mild",
    "scratch",
    "headache",
    "cold",
    "cough",
    "sore throat",
]

ADVICE = {
    "critical": "Call 108 immediately. Do NOT move the patient unless there is immediate danger. Keep them still and conscious if possible.",
    "high": "Seek emergency care right away. Call 108 or get to the nearest hospital. Monitor vitals.",
    "medium": "Go to the nearest hospital or clinic. If symptoms worsen, call 108.",
    "low": "Monitor the situation. Visit a pharmacy or general physician if symptoms persist.",
}

WARNING_SIGNS = [
    "Shortness of breath",
    "Pain spreading to arm, jaw, shoulder, neck, or back",
    "Sweating, nausea, dizziness, or faintness",
    "Chest pressure, tightness, or squeezing",
    "Fast or irregular heartbeat",
    "Symptoms getting worse or not improving in a few minutes",
    "Blue lips, confusion, or near-fainting",
]

HOME_REMEDIES = [
    "Stop all activity and sit upright in a position that feels easiest to breathe.",
    "Do not drive yourself if you feel faint, weak, or pain is severe.",
    "If prescribed nitroglycerin, take it exactly as prescribed.",
    "If safe for you medically, chew aspirin while waiting for help.",
]


class EmergencyAgentError(Exception):
    pass


@dataclass
class SeverityResult:
    severity: str
    score: int
    keywords: list[str]
    advice: str
    call_emergency: bool


class EmergencyResponseAgent:
    def __init__(self, maps_api_key: str | None = None):
        self.maps_api_key = (maps_api_key or GOOGLE_MAPS_API_KEY).strip()

    def assess_emergency(self, description: str) -> dict[str, Any]:
        text = (description or "").strip()
        if not text:
            raise EmergencyAgentError("description is required")

        severity = self._classify_severity(text)
        emergency_message = self._build_emergency_message(text, severity)

        return {
            "originalDescription": text,
            "severity": severity.severity,
            "score": severity.score,
            "keywords": severity.keywords,
            "advice": severity.advice,
            "callEmergency": severity.call_emergency,
            "warningSigns": WARNING_SIGNS,
            "homeRemedies": HOME_REMEDIES,
            "showEmergencyButton": True,
            "emergencyMessage": emergency_message,
        }

    def activate_emergency(
        self,
        lat: float,
        lng: float,
        description: str = "",
        radius: int = 5000,
    ) -> dict[str, Any]:
        if not self.maps_api_key:
            raise EmergencyAgentError("GOOGLE_MAPS_API_KEY is not configured.")

        hospitals = self._fetch_nearby_hospitals(lat, lng, radius)
        severity = self._classify_severity(description) if description.strip() else SeverityResult(
            severity="high",
            score=70,
            keywords=[],
            advice=ADVICE["high"],
            call_emergency=True,
        )

        email_subject = self._build_emergency_subject(description, severity)
        emergency_message = self._build_emergency_message(description, severity, lat, lng, hospitals)

        return {
            "liveTracking": True,
            "location": {
                "lat": lat,
                "lng": lng,
                "label": f"{lat:.5f}, {lng:.5f}",
                "mapUrl": f"https://maps.google.com/?q={lat},{lng}",
                "mapEmbedUrl": f"https://maps.google.com/maps?q={lat},{lng}&z=15&output=embed",
            },
            "severity": severity.severity,
            "hospitals": hospitals,
            "ambulanceNumber": "108",
            "share": {
                "whatsappUrl": f"https://wa.me/?text={requests.utils.quote(emergency_message)}",
                "emailSubject": email_subject,
                "emailBody": emergency_message,
                "copyMessage": emergency_message,
            },
            "advice": severity.advice,
        }

    def _classify_severity(self, description: str) -> SeverityResult:
        text = self._normalize(description)
        critical = self._match_keywords(text, CRITICAL_KEYWORDS)
        high = self._match_keywords(text, HIGH_KEYWORDS)
        medium = self._match_keywords(text, MEDIUM_KEYWORDS)
        low = self._match_keywords(text, LOW_KEYWORDS)

        if critical:
            score = min(100, 85 + len(critical) * 5)
            return SeverityResult("critical", score, critical, ADVICE["critical"], True)
        if high:
            score = min(84, 60 + len(high) * 8)
            return SeverityResult("high", score, high, ADVICE["high"], True)
        if medium:
            score = min(59, 35 + len(medium) * 6)
            return SeverityResult("medium", score, medium, ADVICE["medium"], False)
        if low:
            score = min(34, 15 + len(low) * 5)
            return SeverityResult("low", score, low, ADVICE["low"], False)

        return SeverityResult("medium", 40, [], ADVICE["medium"], False)

    def _fetch_nearby_hospitals(self, lat: float, lng: float, radius: int) -> list[dict[str, Any]]:
        response = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={
                "location": f"{lat},{lng}",
                "radius": max(500, min(radius, 20000)),
                "type": "hospital",
                "key": self.maps_api_key,
            },
            timeout=30,
        )
        payload = response.json()
        status = payload.get("status")
        if status not in {"OK", "ZERO_RESULTS"}:
            raise EmergencyAgentError(f"Google Places API error: {status}")

        hospitals: list[dict[str, Any]] = []
        for item in payload.get("results", [])[:8]:
            dest = item.get("geometry", {}).get("location", {})
            d_lat = float(dest.get("lat", 0))
            d_lng = float(dest.get("lng", 0))
            dist = self._haversine(lat, lng, d_lat, d_lng)
            hospitals.append(
                {
                    "placeId": item.get("place_id"),
                    "name": item.get("name", "Unknown Hospital"),
                    "address": item.get("vicinity") or item.get("formatted_address") or "",
                    "distance": dist,
                    "distanceLabel": f"{round(dist)} m" if dist < 1000 else f"{dist / 1000:.1f} km",
                    "rating": item.get("rating"),
                    "isOpen": item.get("opening_hours", {}).get("open_now"),
                    "location": {"lat": d_lat, "lng": d_lng},
                    "directionsUrl": f"https://www.google.com/maps/dir/{lat},{lng}/{d_lat},{d_lng}",
                    "callUrl": "tel:108",
                }
            )

        hospitals.sort(key=lambda h: h.get("distance", 0))
        return hospitals

    def _build_emergency_message(
        self,
        description: str,
        severity: SeverityResult,
        lat: float | None = None,
        lng: float | None = None,
        hospitals: list[dict[str, Any]] | None = None,
    ) -> str:
        short_desc = (description or "Medical emergency reported.").strip()
        if len(short_desc) > 260:
            short_desc = short_desc[:257] + "..."

        time_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        location_url = f"https://maps.google.com/?q={lat},{lng}" if lat is not None and lng is not None else "Not available"

        nearest_name = "Not available"
        nearest_distance = ""
        if hospitals:
            nearest = hospitals[0]
            nearest_name = str(nearest.get("name") or "Unknown hospital")
            nearest_distance = str(nearest.get("distanceLabel") or "").strip()

        lines = [
            "EMERGENCY ALERT",
            "",
            f"Time: {time_label}",
            f"Severity: {severity.severity.upper()} (score {severity.score}/100)",
            f"Reported situation: {short_desc}",
            "",
            "Immediate advice:",
            f"- {severity.advice}",
            "- Call ambulance immediately: 108",
            "",
            "Live location:",
            f"- {location_url}",
            "",
            "Nearest hospital:",
            f"- {nearest_name}{f' ({nearest_distance})' if nearest_distance else ''}",
            "",
            "Please contact me urgently and share this with nearby responders.",
        ]
        return "\n".join(lines)

    def _build_emergency_subject(self, description: str, severity: SeverityResult) -> str:
        description_tokens = [token for token in re.findall(r"[a-zA-Z]+", description or "") if len(token) > 2]
        keyword = description_tokens[0].capitalize() if description_tokens else "Medical"
        return f"[{severity.severity.upper()}] Emergency Alert - {keyword} - Immediate Attention Needed"

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^a-z0-9 ]", " ", text.lower())

    def _match_keywords(self, text: str, bank: list[str]) -> list[str]:
        return [kw for kw in bank if kw in text]

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius * math.asin(math.sqrt(h))
