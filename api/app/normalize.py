"""Normalisation used for duplicate detection."""

import re
import unicodedata
from urllib.parse import urlparse

_STOP = {"the", "of", "and", "&"}


def domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    raw = website.strip().lower()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).hostname or ""
    host = host.removeprefix("www.")
    return host or None


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = [w for w in text.split() if w not in _STOP]
    return " ".join(words)


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e or None


def normalize_linkedin(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    parsed = urlparse(u if "://" in u else "https://" + u)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"https://{host}{path}".lower()
