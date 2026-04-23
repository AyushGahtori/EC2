import ipaddress
import re
from typing import Literal


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
HASH_PATTERN = re.compile(r"^[a-f0-9]{32}$|^[a-f0-9]{40}$|^[a-f0-9]{64}$", re.IGNORECASE)
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

DISALLOWED_TLDS = {
    "exe", "dll", "sys", "bat", "cmd", "ps1", "psm1", "psd1",
    "vbs", "js", "jse", "vbe", "wsf", "wsh",
    "msi", "msp", "scr", "cpl", "lnk", "jar", "war", "ear", "apk", "ipa",
}

PROCESS_EXTENSIONS = {
    "exe", "dll", "sys", "bat", "cmd", "ps1", "psm1", "psd1",
    "vbs", "js", "jse", "vbe", "wsf", "wsh",
    "msi", "msp", "scr", "cpl", "lnk", "jar", "war", "ear", "apk", "ipa",
}


def _strip_port(value: str) -> str:
    if not value:
        return value
    if ":" in value and value.count(":") == 1:
        host, port = value.split(":", 1)
        if port.isdigit():
            return host
    return value


def is_valid_domain(value: str) -> bool:
    if not value:
        return False
    raw = value.strip().lower().strip(".")
    if not raw:
        return False
    if URL_PATTERN.match(raw):
        return False
    host = _strip_port(raw)
    if not host or host.startswith("-") or host.endswith("-"):
        return False
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return False
    if not DOMAIN_PATTERN.match(host):
        return False
    tld = host.rsplit(".", 1)[-1].lower()
    return tld not in DISALLOWED_TLDS


def _looks_like_process(value: str) -> bool:
    raw = value.strip().lower()
    if not raw:
        return False
    if URL_PATTERN.match(raw):
        return False
    raw = _strip_port(raw)
    if "\\" in raw or "/" in raw:
        ext = raw.rsplit(".", 1)[-1]
        return ext in PROCESS_EXTENSIONS
    if "." in raw:
        ext = raw.rsplit(".", 1)[-1]
        return ext in PROCESS_EXTENSIONS
    return False


def classify_ioc(value: str) -> Literal["ip", "url", "hash", "domain", "process", "unknown"]:
    if not value:
        return "unknown"
    raw = value.strip()
    if not raw:
        return "unknown"
    if URL_PATTERN.match(raw):
        return "url"
    host = _strip_port(raw)
    try:
        ipaddress.ip_address(host)
        return "ip"
    except ValueError:
        pass
    if HASH_PATTERN.match(raw):
        return "hash"
    if _looks_like_process(raw):
        return "process"
    if is_valid_domain(raw):
        return "domain"
    return "unknown"
