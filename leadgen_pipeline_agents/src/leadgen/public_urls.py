"""Reject internal network destinations before reading untrusted business URLs."""

import ipaddress
import socket
from urllib.parse import urlsplit


def validate_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("Evidence URL must use HTTP or HTTPS")
    if parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise ValueError("Evidence URL contains credentials or a non-web port")
    host = parts.hostname.rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Evidence URL points to an internal host")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(row[4][0])
                for row in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
            ]
        except socket.gaierror as error:
            raise ValueError("Evidence host could not be resolved") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Evidence URL points to a non-public network")
