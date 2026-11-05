"""Is an address inside the home network? Used to enforce 'no data leaves home unless opted in' (§2, D-1)."""
import ipaddress
import socket
from urllib.parse import urlsplit

_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def _ip_is_local(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local


def is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    try:
        return _ip_is_local(host)
    except ValueError:
        pass  # not a literal IP
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host:
        return True  # single-label names = Docker/Unraid container names or LAN hosts
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(addrs) and all(_ip_is_local(a) for a in addrs)
