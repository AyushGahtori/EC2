import httpx
import json
from typing import Dict, Any, List
from app.config import get_settings
from app.utils import get_logger

logger = get_logger(__name__)


class GemmaService:
    def __init__(self):
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.ollama_timeout

    async def analyze(self, log_text: str, vt_results: List[Dict]) -> Dict[str, Any]:
        prompt = self._build_prompt(log_text, vt_results)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior SOC cybersecurity analyst. "
                        "Always respond with ONLY valid JSON - no markdown, no explanation, no code fences. "
                        "Never include text outside the JSON object."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 1024}
        }
        try:
            logger.info(f"Calling Ollama: {self.base_url}/api/chat model={self.model}")
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                if r.status_code == 200:
                    raw = r.json()
                    text = raw["message"]["content"]
                    logger.info(f"Ollama responded ({len(text)} chars)")
                    return self._parse_llm_response(text)
                else:
                    logger.error(f"Ollama error {r.status_code}: {r.text[:300]}")
                    return self._fallback_analysis(log_text, vt_results)
        except httpx.ConnectError:
            logger.error(f"Cannot connect to Ollama at {self.base_url} - is it running?")
            return self._fallback_analysis(log_text, vt_results)
        except httpx.ReadTimeout:
            logger.error(f"Ollama timed out after {self.timeout}s")
            return self._fallback_analysis(log_text, vt_results)
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            return self._fallback_analysis(log_text, vt_results)

    def _build_prompt(self, log_text: str, vt_results: List[Dict]) -> str:
        vt_summary = json.dumps(vt_results, indent=2) if vt_results else "No VirusTotal data available."
        return f"""Analyze this security log and VirusTotal data. Return ONLY a JSON object - no markdown, no extra text.

## Security Log:
{log_text}

## VirusTotal Data:
{vt_summary}

Return this exact JSON (values filled in, no placeholders):
{{
  "threat": "<specific threat type>",
  "risk": "<Critical|High|Medium|Low>",
  "confidence": <integer 0-100>,
  "action": "<specific remediation action>",
  "summary": "<2-3 sentence technical analysis>",
  "iocs": ["<IOC values>"],
  "mitre_tactic": "<MITRE ATT&CK tactic>"
}}"""

    def _parse_llm_response(self, text: str) -> Dict[str, Any]:
        try:
            clean = text.strip()
            # Strip markdown fences
            if "```" in clean:
                parts = clean.split("```")
                for part in parts:
                    p = part.strip().lstrip("json").strip()
                    if p.startswith("{"):
                        clean = p
                        break
            # Find JSON boundaries
            start = clean.find("{")
            end = clean.rfind("}") + 1
            if start != -1 and end > start:
                clean = clean[start:end]
            result = json.loads(clean)
            # Normalize confidence to int
            if isinstance(result.get("confidence"), str):
                result["confidence"] = int("".join(filter(str.isdigit, result["confidence"])) or "0")
            return result
        except Exception as e:
            logger.error(f"LLM parse error: {e} | raw: {text[:300]}")
            return {
                "threat": "Parse Error",
                "risk": "Unknown",
                "confidence": 0,
                "action": "Manual review required - LLM response unparseable",
                "summary": text[:300] if text else "No response",
                "iocs": [],
                "mitre_tactic": "Unknown"
            }

    def _fallback_analysis(self, log_text: str, vt_results: List[Dict]) -> Dict[str, Any]:
        """Rule-based fallback when Ollama unreachable."""
        log_lower = log_text.lower()
        malicious_count = sum(r.get("malicious", 0) for r in vt_results)
        max_score = max((r.get("threat_score", 0) for r in vt_results), default=0)

        if any(kw in log_lower for kw in ["failed login", "brute force", "authentication failure", "invalid password", "failed password"]):
            threat, risk, confidence, action = "Brute Force Attack", "High", 85, "Block source IP, enforce account lockout policy"
        elif any(kw in log_lower for kw in ["ransomware", "encrypted", ".locked", "ransom", "shadow copies"]):
            threat, risk, confidence, action = "Ransomware Activity", "Critical", 90, "Isolate affected systems immediately, initiate IR plan"
        elif any(kw in log_lower for kw in ["c2", "command and control", "beacon", "callback", "beaconing"]):
            threat, risk, confidence, action = "C2 Communication", "Critical", 88, "Block C2 endpoint, isolate host, forensic investigation"
        elif any(kw in log_lower for kw in ["phishing", "suspicious email", "malicious attachment", "credential harvester"]):
            threat, risk, confidence, action = "Phishing Attempt", "High", 80, "Block sender, quarantine email, user awareness training"
        elif any(kw in log_lower for kw in ["sql injection", "xss", "cross-site", "directory traversal", "lfi", "rfi"]):
            threat, risk, confidence, action = "Web Application Attack", "High", 82, "Block attacker IP, patch vulnerability, review WAF rules"
        elif any(kw in log_lower for kw in ["port scan", "nmap", "scanning", "enumeration", "reconnaissance"]):
            threat, risk, confidence, action = "Reconnaissance / Port Scan", "Medium", 75, "Block scanner IP, review firewall rules"
        elif any(kw in log_lower for kw in ["privilege escalation", "sudo", "root shell", "administrator"]):
            threat, risk, confidence, action = "Privilege Escalation", "High", 78, "Revoke elevated access, audit sudo logs, investigate user"
        elif any(kw in log_lower for kw in ["exfiltration", "large transfer", "data upload"]):
            threat, risk, confidence, action = "Data Exfiltration", "Critical", 82, "Block outbound connection, preserve evidence, notify DLP team"
        elif malicious_count > 5 or max_score > 50:
            threat, risk, confidence, action = "Known Malicious Indicator", "High", 78, "Block identified IOCs, investigate affected systems"
        elif malicious_count > 0:
            threat, risk, confidence, action = "Suspicious Activity", "Medium", 60, "Monitor closely, investigate IOCs further"
        else:
            threat, risk, confidence, action = "Unknown / Informational", "Low", 40, "Continue monitoring, no immediate action required"

        iocs = [r["value"] for r in vt_results if r.get("malicious", 0) > 0]
        settings = get_settings()
        return {
            "threat": threat,
            "risk": risk,
            "confidence": confidence,
            "action": action,
            "summary": (
                f"Rule-based analysis (Ollama offline) detected {threat.lower()} pattern. "
                f"{len(vt_results)} IOC(s) checked via VirusTotal - {malicious_count} malicious flag(s). "
                f"Start Ollama: ollama run {settings.ollama_model}"
            ),
            "iocs": iocs,
            "mitre_tactic": "N/A (Ollama offline)"
        }
