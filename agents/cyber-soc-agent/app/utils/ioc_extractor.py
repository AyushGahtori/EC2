import re
from dataclasses import dataclass, field
from typing import List
from app.utils.ioc_classifier import is_valid_domain


@dataclass
class Indicators:
    ips: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    hashes: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    processes: List[str] = field(default_factory=list)


def extract_indicators(text: str) -> Indicators:
    """Extract IPs, URLs, hashes, and domains from log text."""
    indicators = Indicators()

    # IPs (IPv4)
    ip_pattern = r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    indicators.ips = list(set(re.findall(ip_pattern, text)))

    # URLs
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    raw_urls = re.findall(url_pattern, text)

    def _clean_url(url: str) -> str:
        # Strip common trailing artifacts and punctuation from log formatting
        cleaned = url.rstrip("\"'`>)]};")
        cleaned = cleaned.rstrip(".,")
        return cleaned

    indicators.urls = list(set(u for u in (_clean_url(u) for u in raw_urls) if u))

    # MD5
    md5_pattern = r'\b[a-fA-F0-9]{32}\b'
    # SHA1
    sha1_pattern = r'\b[a-fA-F0-9]{40}\b'
    # SHA256
    sha256_pattern = r'\b[a-fA-F0-9]{64}\b'

    hashes = []
    hashes.extend(re.findall(sha256_pattern, text))
    hashes.extend(re.findall(sha1_pattern, text))
    hashes.extend(re.findall(md5_pattern, text))
    indicators.hashes = list(set(hashes))

    # Domains (not IPs, not part of URLs, exclude common executable/script extensions)
    domain_pattern = r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+(?:[a-zA-Z]{2,63})\b'
    raw_domains = re.findall(domain_pattern, text)
    # Filter domains already captured in URLs
    url_text = ' '.join(indicators.urls)
    indicators.domains = list(set(
        d for d in raw_domains
        if d not in url_text and is_valid_domain(d)
    ))

    # Processes / executables (e.g., powershell.exe, cmd.exe, malware.exe)
    proc_pattern = r'\b[a-zA-Z0-9_.-]+\.(?:exe|dll|sys|bat|cmd|ps1|psm1|psd1|vbs|js|jse|vbe|wsf|wsh|msi|msp|scr|cpl|lnk|jar|war|ear|apk|ipa)\b'
    indicators.processes = list(set(re.findall(proc_pattern, text, flags=re.IGNORECASE)))

    return indicators
