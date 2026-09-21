from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


MAX_URL_LENGTH = 2048


class UnsafeUrlError(ValueError):
    """Raised when a submitted URL is malformed or unsafe to fetch."""


def _is_forbidden_host(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def validate_listing_url(value: str) -> str:
    if not isinstance(value, str):
        raise UnsafeUrlError("L’URL doit être une chaîne de caractères.")
    normalized = value.strip()
    if not normalized:
        raise UnsafeUrlError("Saisissez l’URL publique d’une annonce.")
    if len(normalized) > MAX_URL_LENGTH:
        raise UnsafeUrlError("L’URL est trop longue.")

    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("L’URL n’est pas valide.") from exc

    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Seules les URL HTTP et HTTPS sont acceptées.")
    if not parsed.hostname:
        raise UnsafeUrlError("L’URL doit contenir un nom de domaine.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("Les URL contenant des identifiants sont refusées.")
    if _is_forbidden_host(parsed.hostname):
        raise UnsafeUrlError("Les adresses locales ou privées sont refusées.")

    default_port = (parsed.scheme == "https" and port in {None, 443}) or (
        parsed.scheme == "http" and port in {None, 80}
    )
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = hostname if default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))
