import asyncio
import re
from collections import Counter
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from app.services import VirusTotalService, GemmaService
from app.utils import extract_indicators, get_logger, classify_ioc

logger = get_logger(__name__)

FAILED_LOGIN_KEYWORDS = [
    "failed login",
    "failed password",
    "authentication failure",
    "invalid password"
]
POWERSHELL_KEYWORDS = ["powershell", "pwsh"]
POWERSHELL_DOWNLOAD_KEYWORDS = [
    "invoke-webrequest",
    "iwr",
    "wget",
    "curl",
    "downloadstring",
    "downloadfile",
    "new-object net.webclient",
    "bitsadmin",
    "start-bitstransfer",
    "invoke-expression",
    "iex"
]
PRIV_ESC_KEYWORDS = [
    "privilege escalation",
    "sudo",
    "sudoers",
    "usermod",
    "adduser",
    "addgroup",
    "administrator",
    "admin group",
    "root shell",
    "setuid",
    "elevated privileges",
    "security policy changed",
    "user added to",
    "group added"
]
EXFIL_VOLUME_HINTS = ["outbound", "egress", "upload", "transfer", "sent", "bytes"]
EXFIL_LARGE_BYTES = 50 * 1024 * 1024
IPV4_PATTERN = r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

SUSPICIOUS_PROCESS_KEYWORDS = [
    "powershell.exe", "pwsh", "cmd.exe", "wmic.exe", "rundll32.exe", "regsvr32.exe",
    "mshta.exe", "wscript.exe", "cscript.exe", "schtasks.exe", "bitsadmin",
    "certutil.exe", "psexec.exe", "procdump.exe", "whoami", "net.exe", "net1.exe",
    "mimikatz", "secretsdump", "lsass.exe"
]
ENCODED_POWERSHELL_KEYWORDS = [
    "-enc", "-encodedcommand", "frombase64string", "base64", "iex", "invoke-expression"
]
SAM_ACCESS_KEYWORDS = [
    "sam", "security accounts manager", "hklm\\sam", "reg save", "ntds.dit",
    "secretsdump", "pwdump", "samdump", "lsass", "mimikatz"
]
C2_KEYWORDS = [
    "command and control", "c2", "beacon", "beaconing", "callback",
    "reverse shell", "botnet", "cobalt strike", "sliver", "mythic", "teamserver"
]
BENIGN_EVENT_IDS = {7040, 37, 41}
TRUSTED_DOMAINS = {"time.windows.com", "microsoft.com"}
TRUSTED_IPS = {"40.81.94.65"}
SYSTEM_USERS = {"system", "nt authority\\system", "localsystem", "local system"}
SEVERITY_WEIGHTS = {
    "information": 0.5,
    "warning": 1.0,
    "error": 1.4,
    "critical": 1.6,
    "audit failure": 1.2,
    "audit success": 0.8,
    "verbose": 0.3,
}
THREAT_MITRE = {
    "Brute Force Attack": {"tactic": "Credential Access", "technique": "T1110 Brute Force"},
    "Remote Code Execution": {"tactic": "Execution", "technique": "T1059 Command and Scripting Interpreter"},
    "Privilege Escalation": {"tactic": "Privilege Escalation", "technique": "T1068 Exploitation for Privilege Escalation"},
    "Data Exfiltration": {"tactic": "Exfiltration", "technique": "T1041 Exfiltration Over C2 Channel"}
}
THREAT_REASON_PHRASES = {
    "Brute Force Attack": "failed logins",
    "Remote Code Execution": "malicious PowerShell execution",
    "Privilege Escalation": "privilege escalation activity",
    "Data Exfiltration": "data exfiltration activity"
}
THREAT_SEQUENCE_LABELS = {
    "Brute Force Attack": "failed login bursts",
    "Remote Code Execution": "PowerShell download execution",
    "Privilege Escalation": "privilege escalation / admin changes",
    "Data Exfiltration": "large outbound transfer activity"
}


class CyberAgent:
    def __init__(self):
        self.vt = VirusTotalService()
        self.llm = GemmaService()
        self._history: List[Dict] = []

    def _risk_rank(self, risk: str) -> int:
        return RISK_ORDER.get((risk or "").lower(), 0)

    def _max_risk(self, *risks: str) -> str:
        best = "Unknown"
        best_rank = -1
        for r in risks:
            if not r:
                continue
            rank = self._risk_rank(r)
            if rank > best_rank:
                best_rank = rank
                best = r
        return best

    def _failed_login_ip_counts(self, log_text: str) -> Dict[str, int]:
        counts: Dict[str, int] = Counter()
        for line in log_text.splitlines():
            lower = line.lower()
            if any(k in lower for k in FAILED_LOGIN_KEYWORDS):
                for ip in re.findall(IPV4_PATTERN, line):
                    counts[ip] += 1
        return counts

    def _bucket_iocs(self, iocs: List[str]) -> Dict[str, List[str]]:
        buckets = {"ips": [], "urls": [], "hashes": [], "domains": [], "processes": []}
        for raw in iocs:
            if not raw or raw == "No structured IOC found":
                continue
            value = raw.strip()
            ioc_type = classify_ioc(value)
            if ioc_type == "ip":
                buckets["ips"].append(value)
            elif ioc_type == "url":
                buckets["urls"].append(value)
            elif ioc_type == "hash":
                buckets["hashes"].append(value)
            elif ioc_type == "domain":
                buckets["domains"].append(value)
            elif ioc_type == "process":
                buckets["processes"].append(value)
        # Deduplicate
        for k in buckets:
            buckets[k] = list(dict.fromkeys(buckets[k]))
        return buckets

    def _first_matching_line(self, lines: List[str], keywords: List[str]) -> Dict[str, Any]:
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(k in lower for k in keywords):
                return {"index": idx, "line": line.strip()}
        return {"index": None, "line": ""}

    def _extract_size_bytes(self, text: str) -> int:
        # Look for explicit byte counts or size units to spot large transfers
        match = re.search(r"\b(\d{7,})\s*(bytes|b)\b", text)
        if match:
            return int(match.group(1))
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb)\b", text)
        if match:
            value = float(match.group(1))
            unit = match.group(2)
            multiplier = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}.get(unit, 1)
            return int(value * multiplier)
        return 0

    def _extract_event_id(self, line: str) -> Optional[int]:
        match = re.search(r"event\s*id\s*[:=]?\s*(\d+)", line, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        match = re.search(r"eventid\s*=\s*(\d+)", line, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _extract_severity(self, line: str) -> str:
        match = re.search(r"severity\s*=\s*([a-zA-Z ]+)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"\blevel\s*[:=]\s*([a-zA-Z ]+)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_user(self, line: str) -> str:
        patterns = [
            r"(?:account name|account|user|username|subject|created by|logon as|logon using)\s*[:=]\s*([^\s,;|]+)",
            r"\buser\s*[:=]\s*([^\s,;|]+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _severity_weight(self, line: str) -> float:
        severity = self._extract_severity(line).lower()
        if severity:
            return SEVERITY_WEIGHTS.get(severity, 1.0)
        return 1.0

    def _is_system_user(self, user: str) -> bool:
        if not user:
            return False
        return user.strip().lower() in SYSTEM_USERS

    def _line_has_suspicious_process(self, line: str) -> bool:
        lower = line.lower()
        return any(k in lower for k in SUSPICIOUS_PROCESS_KEYWORDS)

    def _line_has_encoded_powershell(self, line: str) -> bool:
        lower = line.lower()
        if not any(k in lower for k in POWERSHELL_KEYWORDS):
            return False
        return any(k in lower for k in ENCODED_POWERSHELL_KEYWORDS)

    def _detect_encoded_powershell(self, lines: List[str]) -> Dict[str, Any]:
        for idx, line in enumerate(lines):
            if self._line_has_encoded_powershell(line):
                return {
                    "found": True,
                    "index": idx,
                    "line": line.strip(),
                    "weight": self._severity_weight(line),
                }
        return {}

    def _detect_sam_access(self, lines: List[str]) -> Dict[str, Any]:
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(k in lower for k in SAM_ACCESS_KEYWORDS):
                return {
                    "found": True,
                    "index": idx,
                    "line": line.strip(),
                    "weight": self._severity_weight(line),
                }
        return {}

    def _detect_c2_connection(self, lines: List[str]) -> Dict[str, Any]:
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(k in lower for k in C2_KEYWORDS):
                return {
                    "found": True,
                    "index": idx,
                    "line": line.strip(),
                    "weight": self._severity_weight(line),
                }
        return {}

    def _detect_benign_windows_event(self, lines: List[str]) -> bool:
        for line in lines:
            event_id = self._extract_event_id(line)
            if event_id in BENIGN_EVENT_IDS:
                return True
            lower = line.lower()
            if "kernel-power" in lower or "time.windows.com" in lower:
                return True
        return False

    def _whitelist_hits(self, indicators_payload: Dict[str, List[str]], log_text: str) -> List[str]:
        hits: List[str] = []
        for ip in indicators_payload.get("ips", []):
            if ip in TRUSTED_IPS:
                hits.append(ip)
        for domain in indicators_payload.get("domains", []):
            dom = domain.lower()
            if dom in TRUSTED_DOMAINS or any(dom.endswith("." + d) for d in TRUSTED_DOMAINS):
                hits.append(domain)
        lower = log_text.lower()
        for dom in TRUSTED_DOMAINS:
            if dom in lower and dom not in hits:
                hits.append(dom)
        for ip in TRUSTED_IPS:
            if ip in lower and ip not in hits:
                hits.append(ip)
        return list(dict.fromkeys(hits))

    def _compact_vt_results(self, vt_results: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
        if not vt_results:
            return []
        sorted_results = sorted(
            vt_results,
            key=lambda r: (r.get("malicious", 0), r.get("suspicious", 0), r.get("threat_score", 0)),
            reverse=True,
        )
        compact = []
        for r in sorted_results[:limit]:
            compact.append({
                "type": r.get("type"),
                "value": r.get("value"),
                "malicious": r.get("malicious", 0),
                "suspicious": r.get("suspicious", 0),
                "threat_score": r.get("threat_score", 0),
                "status": r.get("status"),
            })
        return compact

    def _truncate_log_for_llm(self, log_text: str, max_chars: int = 4000) -> str:
        if len(log_text) <= max_chars:
            return log_text
        head = log_text[:3000]
        tail = log_text[-800:]
        return head + "\n...[truncated]...\n" + tail

    def _behavioral_assessment(self, log_text: str) -> Dict[str, Any]:
        counts = self._failed_login_ip_counts(log_text)
        if not counts:
            return {}
        ip, attempts = max(counts.items(), key=lambda x: x[1])
        if attempts < 2:
            return {}

        lines = log_text.splitlines()
        match = self._first_matching_line(lines, FAILED_LOGIN_KEYWORDS)
        actions = [
            "Firewall block the source IP",
            "Enable fail2ban or equivalent rate limiting",
            "Disable password SSH login (use keys)",
            "Monitor for lateral movement"
        ]
        return {
            "threat": "Brute Force Attack",
            "risk": "High",
            "reason": f"Multiple failed login attempts detected from same IP ({ip})",
            "action": "\n".join(f"- {a}" for a in actions),
            "actions": actions,
            "mitre_tactic": THREAT_MITRE["Brute Force Attack"]["tactic"],
            "mitre_technique": THREAT_MITRE["Brute Force Attack"]["technique"],
            "min_confidence": 85,
            "sequence_index": match.get("index"),
            "sequence_label": THREAT_SEQUENCE_LABELS["Brute Force Attack"],
            "evidence": match.get("line")
        }

    def _detect_powershell_rce(self, log_text: str) -> Dict[str, Any]:
        lines = log_text.splitlines()
        for idx, line in enumerate(lines):
            lower = line.lower()
            if not any(k in lower for k in POWERSHELL_KEYWORDS):
                continue
            has_download = any(k in lower for k in POWERSHELL_DOWNLOAD_KEYWORDS)
            has_url = "http://" in lower or "https://" in lower
            if has_download or has_url:
                actions = [
                    "Monitor PowerShell processes and script block logging",
                    "Block malicious download URLs/IPs",
                    "Isolate affected host for forensic review",
                    "Hunt for persistence mechanisms"
                ]
                return {
                    "threat": "Remote Code Execution",
                    "risk": "High",
                    "reason": "PowerShell download or execution detected (possible payload retrieval)",
                    "action": "\n".join(f"- {a}" for a in actions),
                    "actions": actions,
                    "mitre_tactic": THREAT_MITRE["Remote Code Execution"]["tactic"],
                    "mitre_technique": THREAT_MITRE["Remote Code Execution"]["technique"],
                    "min_confidence": 88,
                    "sequence_index": idx,
                    "sequence_label": THREAT_SEQUENCE_LABELS["Remote Code Execution"],
                    "evidence": line.strip()
                }
        return {}

    def _detect_privilege_escalation(
        self,
        log_text: str,
        suspicious_process_present: bool,
        abnormal_user_present: bool,
        correlated_malicious: bool
    ) -> Dict[str, Any]:
        lines = log_text.splitlines()
        for idx, line in enumerate(lines):
            lower = line.lower()
            if not any(k in lower for k in PRIV_ESC_KEYWORDS):
                continue
            event_id = self._extract_event_id(line)
            if event_id in BENIGN_EVENT_IDS:
                continue
            user = self._extract_user(line)
            abnormal_user = bool(user) and not self._is_system_user(user)
            suspicious_process = suspicious_process_present or self._line_has_suspicious_process(line)
            context_ok = suspicious_process or abnormal_user or abnormal_user_present or correlated_malicious
            if not context_ok:
                continue
            actions = [
                "Audit user privileges and recent admin changes",
                "Revoke unexpected elevated access",
                "Review sudoers and group membership changes"
            ]
            return {
                "threat": "Privilege Escalation",
                "risk": "High",
                "reason": "Privilege escalation indicators detected with suspicious context",
                "action": "\n".join(f"- {a}" for a in actions),
                "actions": actions,
                "mitre_tactic": THREAT_MITRE["Privilege Escalation"]["tactic"],
                "mitre_technique": THREAT_MITRE["Privilege Escalation"]["technique"],
                "min_confidence": 80,
                "sequence_index": idx,
                "sequence_label": THREAT_SEQUENCE_LABELS["Privilege Escalation"],
                "evidence": line.strip()
            }
        return {}

    def _detect_data_exfiltration(self, log_text: str) -> Dict[str, Any]:
        lines = log_text.splitlines()
        for idx, line in enumerate(lines):
            lower = line.lower()
            if "exfiltration" in lower or "data exfil" in lower:
                return self._build_exfil_threat(idx, line, None, explicit=True)

            size_bytes = self._extract_size_bytes(lower)
            has_volume_hint = any(k in lower for k in EXFIL_VOLUME_HINTS)
            has_outbound_hint = any(k in lower for k in ["outbound", "egress", "upload"])
            if size_bytes >= EXFIL_LARGE_BYTES and (has_volume_hint or has_outbound_hint):
                return self._build_exfil_threat(idx, line, size_bytes, explicit=False)

            if "large" in lower and has_outbound_hint:
                return self._build_exfil_threat(idx, line, None, explicit=False)
        return {}

    def _build_exfil_threat(self, idx: int, line: str, size_bytes: Any, explicit: bool) -> Dict[str, Any]:
        size_note = ""
        reason = "Outbound data exfiltration indicators detected"
        if size_bytes:
            size_mb = max(1, round(size_bytes / (1024 ** 2)))
            size_note = f" (~{size_mb} MB)"
            reason = f"Large outbound data transfer detected{size_note}"
        elif not explicit:
            reason = "Large outbound data transfer detected"
        actions = [
            "Investigate outbound traffic and data transfer destinations",
            "Block suspicious egress channels",
            "Engage DLP/IR team and preserve evidence"
        ]
        return {
            "threat": "Data Exfiltration",
            "risk": "Critical",
            "reason": reason,
            "action": "\n".join(f"- {a}" for a in actions),
            "actions": actions,
            "mitre_tactic": THREAT_MITRE["Data Exfiltration"]["tactic"],
            "mitre_technique": THREAT_MITRE["Data Exfiltration"]["technique"],
            "min_confidence": 85,
            "sequence_index": idx,
            "sequence_label": THREAT_SEQUENCE_LABELS["Data Exfiltration"],
            "evidence": line.strip()
        }

    def _detect_threats(self, log_text: str) -> List[Dict[str, Any]]:
        threats: List[Dict[str, Any]] = []
        lines = log_text.splitlines()
        suspicious_process_present = any(self._line_has_suspicious_process(line) for line in lines)
        abnormal_user_present = any(
            (self._extract_user(line) and not self._is_system_user(self._extract_user(line)))
            for line in lines
        )

        brute = self._behavioral_assessment(log_text)
        if brute:
            threats.append(brute)
        rce = self._detect_powershell_rce(log_text)
        if rce:
            threats.append(rce)
        exfil = self._detect_data_exfiltration(log_text)
        if exfil:
            threats.append(exfil)

        encoded_ps = bool(self._detect_encoded_powershell(lines))
        sam_access = bool(self._detect_sam_access(lines))
        c2_conn = bool(self._detect_c2_connection(lines))
        correlated_malicious = sum(1 for flag in [brute, rce, exfil, encoded_ps, sam_access, c2_conn] if flag) >= 2

        priv = self._detect_privilege_escalation(
            log_text,
            suspicious_process_present=suspicious_process_present,
            abnormal_user_present=abnormal_user_present,
            correlated_malicious=correlated_malicious
        )
        if priv:
            threats.append(priv)

        return threats

    def _merge_actions(self, threats: List[Dict[str, Any]], fallback_action: str) -> str:
        actions: List[str] = []
        for threat in threats:
            if threat.get("actions"):
                actions.extend(threat["actions"])
                continue
            action_text = (threat.get("action") or "").strip()
            if not action_text:
                continue
            for line in action_text.splitlines():
                cleaned = line.strip()
                if not cleaned:
                    continue
                if cleaned.startswith("- "):
                    cleaned = cleaned[2:].strip()
                actions.append(cleaned)

        if not actions:
            return fallback_action or "Manual review required"

        seen = set()
        deduped: List[str] = []
        for action in actions:
            key = action.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)

        return "\n".join(f"- {action}" for action in deduped)

    def _build_reason(self, threats: List[Dict[str, Any]], default_reason: str) -> str:
        if not threats:
            return default_reason
        if len(threats) == 1:
            return threats[0].get("reason") or default_reason
        phrases = [THREAT_REASON_PHRASES.get(t["threat"]) for t in threats if THREAT_REASON_PHRASES.get(t["threat"])]
        if not phrases:
            return "Multiple attack patterns detected across stages."
        if len(phrases) == 1:
            joined = phrases[0]
        elif len(phrases) == 2:
            joined = f"{phrases[0]} and {phrases[1]}"
        else:
            joined = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"
        return f"Multiple attack patterns detected including {joined}."

    def _build_summary(self, threats: List[Dict[str, Any]], ai_summary: str) -> str:
        if not threats:
            return ai_summary or ""
        ordered = sorted(
            threats,
            key=lambda t: t.get("sequence_index") if t.get("sequence_index") is not None else 10 ** 9
        )
        names = [t["threat"] for t in ordered]
        chain = " -> ".join(t.get("sequence_label", t["threat"]) for t in ordered)
        if len(ordered) > 1:
            return (
                f"Multi-stage attack detected across {len(ordered)} stages: {', '.join(names)}. "
                f"Sequence observed: {chain}."
            )
        return ai_summary or f"Single-stage attack detected: {names[0]}. Observed activity: {chain}."

    def _build_mitre_fields(self, threats: List[Dict[str, Any]], analysis: Dict[str, Any]) -> Dict[str, Any]:
        if threats:
            mapping = [
                {"threat": t["threat"], "tactic": t.get("mitre_tactic", ""), "technique": t.get("mitre_technique", "")}
                for t in threats
            ]
            if len(mapping) == 1:
                return {
                    "mitre_tactic": mapping[0]["tactic"],
                    "mitre_technique": mapping[0]["technique"],
                    "mitre_mapping": mapping
                }
            techniques = [m["technique"] for m in mapping if m.get("technique")]
            return {
                "mitre_tactic": "Multiple",
                "mitre_technique": ", ".join(techniques),
                "mitre_mapping": mapping
            }

        ai_threat = analysis.get("threat") or "Unknown"
        return {
            "mitre_tactic": analysis.get("mitre_tactic", ""),
            "mitre_technique": analysis.get("mitre_technique", ""),
            "mitre_mapping": [
                {"threat": ai_threat, "tactic": analysis.get("mitre_tactic", ""), "technique": analysis.get("mitre_technique", "")}
            ]
        }

    def _derive_attack_type(self, stage_count: int, threat_score: int) -> str:
        if stage_count == 0 and threat_score <= 2:
            return "No Threat"
        return "Multi-Stage Attack" if stage_count > 1 else "Single Attack"

    def _derive_risk(self, threats: List[Dict[str, Any]], base_risk: str) -> str:
        if len(threats) > 1:
            return "Critical"
        if len(threats) == 1:
            threat = threats[0]
            if threat.get("threat") == "Brute Force Attack":
                return self._max_risk(base_risk, "High")
            return self._max_risk(base_risk, threat.get("risk"))
        return base_risk

    def _vt_risk(self, vt_results: List[Dict]) -> str:
        # Ignore skipped lookups (e.g., private IPs)
        filtered = [r for r in vt_results if r.get("vt_lookup") != "skipped"]
        malicious = sum(r.get("malicious", 0) for r in filtered)
        max_score = max((r.get("threat_score", 0) for r in filtered), default=0)
        if malicious > 5 or max_score > 50:
            return "High"
        if malicious > 0:
            return "Medium"
        return "Unknown"

    def _severity_from_score(self, score: int) -> str:
        if score <= 2:
            return "LOW"
        if score <= 6:
            return "MEDIUM"
        if score >= 10:
            return "CRITICAL"
        return "HIGH"

    def _hybrid_confidence(self, ai_conf: Any, behaviors: Any, vt_results: List[Dict]) -> int:
        conf = int(ai_conf or 0)
        min_conf = 0
        if isinstance(behaviors, list):
            min_conf = max((int(b.get("min_confidence", 0)) for b in behaviors), default=0)
        elif isinstance(behaviors, dict):
            min_conf = int(behaviors.get("min_confidence", 0))
        if min_conf:
            conf = max(conf, min_conf)
        if isinstance(behaviors, list) and len(behaviors) > 1:
            conf = max(conf, 90)
        vt_mal = sum(r.get("malicious", 0) for r in vt_results if r.get("vt_lookup") != "skipped")
        if vt_mal > 0:
            conf = min(100, conf + 5)
        if vt_mal > 5:
            conf = min(100, conf + 5)
        return max(0, min(conf, 100))

    def _compute_threat_score(
        self,
        log_text: str,
        indicators_payload: Dict[str, List[str]],
        vt_results: List[Dict[str, Any]],
        behavior_threats: List[Dict[str, Any]]
    ) -> Tuple[int, List[str], List[str], List[str]]:
        lines = log_text.splitlines()
        score = 0
        stages: List[str] = []
        signals: List[str] = []

        # Stage signals from behavioral detections
        for threat in behavior_threats:
            name = threat.get("threat")
            if name and name not in stages:
                stages.append(name)

        encoded = self._detect_encoded_powershell(lines)
        if encoded:
            add = int(round(3 * encoded.get("weight", 1.0)))
            score += add
            signals.append("encoded_powershell")
            if "Encoded PowerShell" not in stages:
                stages.append("Encoded PowerShell")

        priv = next((t for t in behavior_threats if t.get("threat") == "Privilege Escalation"), None)
        if priv:
            weight = self._severity_weight(priv.get("evidence", "") or "")
            add = int(round(3 * (weight or 1.0)))
            score += add
            signals.append("privilege_escalation")

        sam = self._detect_sam_access(lines)
        if sam:
            add = int(round(2 * sam.get("weight", 1.0)))
            score += add
            signals.append("sam_access")
            if "SAM Access" not in stages:
                stages.append("SAM Access")

        c2 = self._detect_c2_connection(lines)
        if c2:
            add = int(round(3 * c2.get("weight", 1.0)))
            score += add
            signals.append("c2_connection")
            if "C2 Connection" not in stages:
                stages.append("C2 Connection")

        malicious_hash = any(
            r.get("type") == "hash" and r.get("malicious", 0) > 0 and r.get("vt_lookup") != "skipped"
            for r in vt_results
        )
        if not malicious_hash:
            if indicators_payload.get("hashes") and ("malware" in log_text.lower() or "trojan" in log_text.lower()):
                malicious_hash = True
        if malicious_hash:
            score += 3
            signals.append("malware_hash")

        if self._detect_benign_windows_event(lines):
            score -= 3
            signals.append("benign_windows_event")

        whitelist_hits = self._whitelist_hits(indicators_payload, log_text)
        if whitelist_hits:
            score -= 2 * len(whitelist_hits)
            signals.append("whitelist_match")

        return score, stages, signals, whitelist_hits

    async def analyze(self, log_text: str) -> Dict[str, Any]:
        logger.info(f"Analyzing log ({len(log_text)} chars)")
        start = datetime.utcnow()

        # 1. Extract IOCs
        indicators = extract_indicators(log_text)
        logger.info(
            f"Extracted — IPs:{len(indicators.ips)} URLs:{len(indicators.urls)} "
            f"Hashes:{len(indicators.hashes)} Domains:{len(indicators.domains)} Processes:{len(indicators.processes)}"
        )

        indicators_payload = {
            "ips": indicators.ips,
            "urls": indicators.urls,
            "hashes": indicators.hashes,
            "domains": indicators.domains,
            "processes": indicators.processes,
        }

        # 2. VirusTotal enrichment (parallel, cap at 5 per type to avoid quota burn)
        vt_tasks = []
        for ip in indicators.ips[:5]:
            vt_tasks.append(self.vt.check_ioc(ip))
        for url in indicators.urls[:3]:
            vt_tasks.append(self.vt.check_ioc(url))
        for h in indicators.hashes[:3]:
            vt_tasks.append(self.vt.check_ioc(h))
        for d in indicators.domains[:3]:
            vt_tasks.append(self.vt.check_ioc(d))

        vt_results = []
        if vt_tasks:
            raw = await asyncio.gather(*vt_tasks, return_exceptions=True)
            vt_results = [r for r in raw if isinstance(r, dict)]
        logger.info(f"VT results: {len(vt_results)}")

        # 3. LLM analysis (trim payload to reduce latency)
        llm_log = self._truncate_log_for_llm(log_text)
        vt_compact = self._compact_vt_results(vt_results)
        analysis = await self.llm.analyze(llm_log, vt_compact)

        # 4. Behavioral detections + threat scoring
        behavior_threats = self._detect_threats(log_text)
        ordered_threats = sorted(
            behavior_threats,
            key=lambda t: t.get("sequence_index") if t.get("sequence_index") is not None else 10 ** 9
        )
        ioc_list = analysis.get("iocs", [])
        if not any(indicators_payload.values()) and ioc_list:
            indicators_payload = self._bucket_iocs(ioc_list)

        threat_score, stages, signals, whitelist_hits = self._compute_threat_score(
            log_text,
            indicators_payload,
            vt_results,
            behavior_threats
        )

        vt_checked = sum(1 for r in vt_results if r.get("vt_lookup") != "skipped")
        vt_failed = sum(1 for r in vt_results if r.get("status") in ("error", "invalid"))
        vt_malicious = sum(
            1 for r in vt_results
            if r.get("vt_lookup") != "skipped" and r.get("malicious", 0) > 0
        )
        vt_suspicious = sum(
            1 for r in vt_results
            if r.get("vt_lookup") != "skipped" and r.get("suspicious", 0) > 0
        )
        vt_max_score = max(
            (r.get("threat_score", 0) for r in vt_results if r.get("vt_lookup") != "skipped"),
            default=0
        )

        vt_adjust = 0
        if vt_malicious > 0 or vt_max_score >= 50:
            vt_adjust = 3
        elif vt_suspicious > 0:
            vt_adjust = 1

        combined_score = threat_score + vt_adjust
        severity = self._severity_from_score(combined_score)

        # Override logic: behavior wins over VT noise
        if threat_score >= 7 and severity in ("LOW", "MEDIUM"):
            severity = "HIGH"
        if threat_score <= 2 and severity in ("HIGH", "CRITICAL"):
            severity = "MEDIUM"

        final_risk = severity.title()
        final_conf = self._hybrid_confidence(analysis.get("confidence", 0), behavior_threats, vt_results)
        if threat_score >= 7:
            final_conf = max(final_conf, 85)
        if len(stages) > 1:
            final_conf = max(final_conf, 90)
        if threat_score <= 2 and vt_malicious == 0:
            final_conf = min(final_conf, 70)

        ai_threat = analysis.get("threat", "Unknown") or "Unknown"
        threat_names = stages[:] if stages else []
        if not threat_names and ai_threat.lower() not in ("unknown", "parse error"):
            threat_names = [ai_threat]
        attack_type = self._derive_attack_type(len(stages), threat_score)
        if attack_type == "No Threat":
            display_threat = "No Threat"
            threat_names = []
        else:
            display_threat = "Multi-Stage Attack" if len(threat_names) > 1 else threat_names[0]

        # 5. Build response
        elapsed = (datetime.utcnow() - start).total_seconds()
        fallback_action = (analysis.get("action") or "").strip()
        if attack_type == "No Threat" and not fallback_action:
            fallback_action = "Continue monitoring; no immediate action required."
        action = self._merge_actions(ordered_threats, fallback_action)

        summary = self._build_summary(ordered_threats, analysis.get("summary", ""))
        if attack_type == "No Threat":
            summary = analysis.get("summary") or "No significant malicious activity detected based on current logs."

        reason = self._build_reason(ordered_threats, "Behavioral correlation with IOC enrichment")
        if whitelist_hits:
            reason = f"{reason} Trusted sources observed: {', '.join(whitelist_hits)}."
        if "benign_windows_event" in signals:
            reason = f"{reason} Benign Windows events detected."
        mitre_fields = self._build_mitre_fields(ordered_threats, analysis)

        structured_iocs = {
            "ips": indicators_payload.get("ips", []),
            "domains": indicators_payload.get("domains", []),
            "hashes": indicators_payload.get("hashes", []),
            "processes": indicators_payload.get("processes", []),
        }
        iocs_list = list(dict.fromkeys(
            structured_iocs["ips"]
            + structured_iocs["domains"]
            + structured_iocs["hashes"]
            + structured_iocs["processes"]
            + indicators_payload.get("urls", [])
            + (analysis.get("iocs", []) if isinstance(analysis.get("iocs", []), list) else [])
        ))

        vt_summary = {
            "checked": vt_checked,
            "malicious": vt_malicious,
            "failed": vt_failed
        }

        result = {
            "id": len(self._history) + 1,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "log_excerpt": log_text[:200],
            "attack_type": attack_type,
            "threats": threat_names,
            "threat": display_threat,
            "risk": final_risk,
            "severity": severity,
            "confidence": final_conf,
            "threat_score": threat_score,
            "action": action,
            "summary": summary,
            "reason": reason,
            "iocs": structured_iocs,
            "iocs_list": iocs_list,
            "mitre_tactic": mitre_fields.get("mitre_tactic", ""),
            "mitre_technique": mitre_fields.get("mitre_technique", ""),
            "mitre_mapping": mitre_fields.get("mitre_mapping", []),
            "vt_results": vt_results,
            "indicators_found": indicators_payload,
            "vt_summary": vt_summary,
            "processing_time_s": round(elapsed, 2)
        }

        self._history.insert(0, result)
        if len(self._history) > 100:
            self._history = self._history[:100]

        logger.info(f"Analysis complete: {result['threat']} | {result['risk']} | {result['confidence']}% | {elapsed:.2f}s")
        return result

    def get_history(self) -> List[Dict]:
        return self._history
