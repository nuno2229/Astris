"""Is an address inside the home network? Used to enforce 'no data leaves home unless opted in' (§2, D-1)."""
import ipaddress
import socket
from urllib.parse import urlsplit

_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def _ip_is_local(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local


_EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "100.100.100.200/32",   # Alibaba Cloud metadata
    "fd00:ec2::254/128",    # AWS IMDS over IPv6
)]


def _embedded_v4(a: ipaddress._BaseAddress):
    """Unwrap IPv4 hidden in IPv6 forms (mapped, NAT64 64:ff9b::/96, 6to4 2002::/16)."""
    if isinstance(a, ipaddress.IPv6Address):
        if a.ipv4_mapped:
            return a.ipv4_mapped
        if a in ipaddress.ip_network("64:ff9b::/96"):
            return ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        if a.sixtofour:
            return a.sixtofour
    return a


def _ip_is_blocked(ip: str) -> bool:
    """Never contacted by the server: link-local (incl. cloud metadata 169.254.169.254), multicast,
    unspecified, reserved and known metadata addresses — also when wrapped in IPv6."""
    for a in {ipaddress.ip_address(ip), _embedded_v4(ipaddress.ip_address(ip))}:
        if a.is_link_local or a.is_multicast or a.is_unspecified or a.is_reserved:
            return True
        if any(a in n for n in _EXTRA_BLOCKED if n.version == a.version):
            return True
    return False


def resolve(host: str) -> set[str]:
    try:
        ipaddress.ip_address(host)
        return {host}
    except ValueError:
        pass
    try:
        return {i[4][0].split("%")[0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def is_blocked_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    addrs = resolve(host) if host else set()
    return not addrs or any(_ip_is_blocked(a) for a in addrs)


class BlockedAddress(Exception):
    pass


def origin_only(url: str) -> str | None:
    """Normalise to scheme://host[:port]. None if the URL carries a path, query, fragment or credentials —
    the LLM endpoint is a server, not an arbitrary URL (limits blind POSTs into the LAN)."""
    try:
        p = urlsplit(url.strip())
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return None
    if p.path not in ("", "/") or p.query or p.fragment:
        return None
    host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    return f"{p.scheme}://{host}" + (f":{port}" if port else "")


def pinned_request(url: str, method: str = "GET", body: bytes | None = None, timeout: float = 5,
                   max_bytes: int = 256 * 1024, headers: dict | None = None) -> tuple[int, bytes]:
    """HTTP without DNS-rebinding: resolve ONCE, refuse if any address is blocked, then connect to that
    exact IP (Host header + TLS SNI/cert check still use the hostname). Never follows redirects.
    `timeout` is a TOTAL wall-clock deadline for the whole exchange, not per socket read."""
    import http.client
    import ssl
    import time

    deadline = time.monotonic() + timeout
    held: list = []  # our own socket reference: http.client drops conn.sock after reading headers

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("deadline exceeded")
        return left

    parts = urlsplit(url)
    host = parts.hostname or ""
    addrs = sorted(resolve(host))
    if not addrs or any(_ip_is_blocked(a) for a in addrs):
        raise BlockedAddress(url)
    ip = addrs[0]
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")

    if parts.scheme == "https":
        ctx = ssl.create_default_context()

        class _Conn(http.client.HTTPSConnection):
            def connect(self):
                raw = socket.create_connection((ip, port), remaining())
                self.sock = ctx.wrap_socket(raw, server_hostname=host)
                held.append(self.sock)
        conn = _Conn(host, port, timeout=timeout, context=ctx)
    else:
        class _Conn(http.client.HTTPConnection):  # type: ignore[no-redef]
            def connect(self):
                self.sock = socket.create_connection((ip, port), remaining())
                held.append(self.sock)
        conn = _Conn(host, port, timeout=timeout)
    import threading

    def _kill():  # watchdog: the deadline holds even while http.client is blocked reading headers
        for sk in held:
            try:
                sk.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sk.close()
            except OSError:
                pass
    dog = threading.Timer(timeout, _kill)
    dog.daemon = True
    dog.start()
    try:
        conn.request(method, path, body=body, headers={"Accept": "application/json", **(headers or {})})
        held[0].settimeout(remaining())
        r = conn.getresponse()
        buf = bytearray()
        while len(buf) < max_bytes:
            held[0].settimeout(remaining())  # a server trickling bytes can't extend the deadline
            chunk = r.read1(min(65536, max_bytes - len(buf)))
            if not chunk:
                break
            buf += chunk
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline exceeded")
        return r.status, bytes(buf)
    except OSError:
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline exceeded")
        raise
    finally:
        dog.cancel()
        conn.close()


def pinned_get(url: str, timeout: float = 5, max_bytes: int = 256 * 1024) -> tuple[int, bytes]:
    return pinned_request(url, "GET", timeout=timeout, max_bytes=max_bytes)


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
