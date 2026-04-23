import httpx
import ipaddress
import time
from typing import Optional, Dict, Any
from app.config import get_settings
from app.utils import get_logger
from app.utils.ioc_classifier import classify_ioc, is_valid_domain

logger = get_logger(__name__)

VT_BASE = "https://www.virustotal.com/api/v3"


class VirusTotalService:
    def __init__(self):
        self.api_key = get_settings().virustotal_api_key
        self.headers = {"x-apikey": self.api_key}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl_s = 1800

    async def _get(self, endpoint: str) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            logger.warning("No VirusTotal API key configured")
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{VT_BASE}/{endpoint}", headers=self.headers)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 404:
                    return {"not_found": True}
                else:
                    logger.error(f"VT error {r.status_code}: {endpoint}")
                    return None
        except Exception as e:
            logger.error(f"VT request failed: {e}")
            return None

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(key)
        if not entry:
            return None
        ts = entry.get("_ts", 0)
        if (time.time() - ts) > self.cache_ttl_s:
            self._cache.pop(key, None)
            return None
        cached = dict(entry)
        cached.pop("_ts", None)
        return cached

    def _cache_set(self, key: str, value: Dict[str, Any]) -> Dict[str, Any]:
        entry = dict(value)
        entry["_ts"] = time.time()
        self._cache[key] = entry
        return value

    async def check_ip(self, ip: str) -> Optional[Dict]:
        if ":" in ip and ip.count(":") == 1:
            host, port = ip.split(":", 1)
            if port.isdigit():
                ip = host
        cache_key = f"ip:{ip}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        # Skip private IPs to avoid meaningless VT lookups
        try:
            if ipaddress.ip_address(ip).is_private:
                return self._cache_set(cache_key, {
                    "ip": ip,
                    "type": "private",
                    "vt_lookup": "skipped",
                    "value": ip,
                    "status": "skipped",
                    "malicious": 0,
                    "suspicious": 0,
                    "total": 0
                })
        except ValueError:
            # If parsing fails, fall back to VT lookup for transparency
            pass
        data = await self._get(f"ip_addresses/{ip}")
        return self._cache_set(cache_key, self._parse_result(data, "ip", ip))

    async def check_url(self, url: str) -> Optional[Dict]:
        cache_key = f"url:{url}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        import base64
        if not url.lower().startswith(("http://", "https://")):
            return self._cache_set(cache_key, {
                "type": "url",
                "value": url,
                "status": "invalid",
                "vt_lookup": "skipped",
                "malicious": 0,
                "suspicious": 0,
                "total": 0
            })
        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        data = await self._get(f"urls/{url_id}")
        return self._cache_set(cache_key, self._parse_result(data, "url", url))

    async def check_hash(self, file_hash: str) -> Optional[Dict]:
        cache_key = f"hash:{file_hash}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        data = await self._get(f"files/{file_hash}")
        return self._cache_set(cache_key, self._parse_result(data, "hash", file_hash))

    async def check_domain(self, domain: str) -> Optional[Dict]:
        clean_domain = domain.strip()
        if ":" in clean_domain and clean_domain.count(":") == 1:
            host, port = clean_domain.split(":", 1)
            if port.isdigit():
                clean_domain = host
        cache_key = f"domain:{clean_domain}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        if not is_valid_domain(clean_domain):
            return self._cache_set(cache_key, {
                "type": "domain",
                "value": clean_domain,
                "status": "invalid",
                "vt_lookup": "skipped",
                "malicious": 0,
                "suspicious": 0,
                "total": 0
            })
        data = await self._get(f"domains/{clean_domain}")
        return self._cache_set(cache_key, self._parse_result(data, "domain", clean_domain))

    async def check_ioc(self, ioc: str) -> Optional[Dict]:
        """Classify IOC and route to appropriate VT endpoint."""
        ioc_type = classify_ioc(ioc)
        if ioc_type == "ip":
            return await self.check_ip(ioc)
        if ioc_type == "url":
            return await self.check_url(ioc)
        if ioc_type == "hash":
            return await self.check_hash(ioc)
        if ioc_type == "domain":
            return await self.check_domain(ioc)
        # Processes or unknown IOCs should not hit VT domains API
        return {
            "type": ioc_type,
            "value": ioc,
            "status": "skipped",
            "vt_lookup": "skipped",
            "malicious": 0,
            "suspicious": 0,
            "total": 0
        }

    def _parse_result(self, data: Optional[Dict], ioc_type: str, value: str) -> Dict:
        if not data:
            return {
                "type": ioc_type,
                "value": value,
                "status": "error",
                "vt_lookup": "failed",
                "malicious": 0,
                "suspicious": 0,
                "total": 0
            }
        if data.get("not_found"):
            return {
                "type": ioc_type,
                "value": value,
                "status": "not_found",
                "vt_lookup": "done",
                "malicious": 0,
                "suspicious": 0,
                "total": 0
            }

        stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
        malicious = stats.get("malicious", 0)
        suspicious = stats.get("suspicious", 0)
        total = sum(stats.values()) if stats else 0

        reputation = data.get("data", {}).get("attributes", {}).get("reputation", 0)
        categories = data.get("data", {}).get("attributes", {}).get("categories", {})

        return {
            "type": ioc_type,
            "value": value,
            "status": "found",
            "vt_lookup": "done",
            "malicious": malicious,
            "suspicious": suspicious,
            "total": total,
            "reputation": reputation,
            "categories": list(categories.values())[:3] if categories else [],
            "threat_score": round((malicious / total * 100) if total > 0 else 0, 1)
        }
