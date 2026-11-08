"""Encryption at rest for the most sensitive things the app stores (P-10): kept statement/receipt files,
notification channel secrets (webhook URLs, bot tokens) and calendar links. AES-256-GCM.

Keys (environment, never in the database):
  ASTRIS_KEY       the current key, 32 random bytes in base64 (make one: python -m app.crypto new-key)
  ASTRIS_OLD_KEYS  optional, comma-separated earlier keys, still accepted for reading while you rotate

Commands (run in the container):
  python -m app.crypto new-key     print a new key
  python -m app.crypto rewrap      encrypt anything still stored in plain form, and re-encrypt with ASTRIS_KEY
                                   anything under an old key (then drop the old key from ASTRIS_OLD_KEYS)
  python -m app.crypto status      how many items are plain / encrypted

The rest of the database (transactions etc.) is protected by disk encryption on the server (README). Without the
key, encrypted files and secrets can't be recovered, including from backups: keep it in your password manager."""
import base64
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ASTRIS1"
PREFIX = "enc1:"


def _parse(k: str) -> bytes:
    raw = base64.urlsafe_b64decode(k.strip() + "=" * (-len(k.strip()) % 4))
    if len(raw) != 32:
        raise ValueError("ASTRIS_KEY must be 32 bytes in base64 (python -m app.crypto new-key)")
    return raw


def _kid(key: bytes) -> bytes:
    return hashlib.sha256(b"astris-kid" + key).digest()[:8]


def current_key() -> bytes | None:
    k = os.environ.get("ASTRIS_KEY", "").strip()
    return _parse(k) if k else None


def _keys() -> dict[bytes, bytes]:
    out = {}
    for k in [os.environ.get("ASTRIS_KEY", "")] + os.environ.get("ASTRIS_OLD_KEYS", "").split(","):
        if k.strip():
            key = _parse(k)
            out[_kid(key)] = key
    return out


def enabled() -> bool:
    return current_key() is not None


def is_encrypted(blob: bytes) -> bool:
    return bytes(blob[:len(MAGIC)]) == MAGIC


def encrypt(data: bytes, aad: bytes = b"") -> bytes:
    key = current_key()
    if key is None:
        return data
    nonce = os.urandom(12)
    return MAGIC + _kid(key) + nonce + AESGCM(key).encrypt(nonce, data, aad)


def decrypt(blob: bytes, aad: bytes = b"") -> bytes:
    blob = bytes(blob)
    if not is_encrypted(blob):
        return blob                     # stored before encryption was turned on
    kid, nonce, ct = blob[7:15], blob[15:27], blob[27:]
    key = _keys().get(kid)
    if key is None:
        raise RuntimeError("This item was encrypted with a key the server doesn't have (check ASTRIS_KEY / ASTRIS_OLD_KEYS)")
    return AESGCM(key).decrypt(nonce, ct, aad)


def needs_rewrap(blob: bytes) -> bool:
    key = current_key()
    if key is None:
        return False
    return not is_encrypted(blob) or bytes(blob)[7:15] != _kid(key)


# text / json helpers (stored as text: "enc1:<base64>", or jsonb: {"enc1": "<base64>"})
def encrypt_text(s: str | None, aad: str = "") -> str | None:
    if s is None or not enabled():
        return s
    return PREFIX + base64.b64encode(encrypt(s.encode(), aad.encode())).decode()


def decrypt_text(s: str | None, aad: str = "") -> str | None:
    if s is None or not s.startswith(PREFIX):
        return s
    return decrypt(base64.b64decode(s[len(PREFIX):]), aad.encode()).decode()


def encrypt_json(obj: dict, aad: str = "") -> dict:
    if not enabled():
        return obj
    return {"enc1": base64.b64encode(encrypt(json.dumps(obj).encode(), aad.encode())).decode()}


def decrypt_json(obj: dict, aad: str = "") -> dict:
    if isinstance(obj, dict) and set(obj) == {"enc1"}:
        return json.loads(decrypt(base64.b64decode(obj["enc1"]), aad.encode()))
    return obj


def _text_blob(s: str | None) -> bytes:
    return base64.b64decode(s[len(PREFIX):]) if s and s.startswith(PREFIX) else (s or "").encode()


def rewrap() -> dict:
    """Encrypt plain items and move old-key items to the current key. Owner connection (all households)."""
    if not enabled():
        raise SystemExit("Set ASTRIS_KEY first (python -m app.crypto new-key)")
    from sqlalchemy import create_engine, text
    from app.config import get_settings
    eng = create_engine(get_settings().owner_db_url)
    n = {"documents": 0, "notification_channels": 0, "calendar_feeds": 0}
    with eng.begin() as c:
        for r in c.execute(text("SELECT id, data FROM documents")).all():
            if needs_rewrap(r.data):
                c.execute(text("UPDATE documents SET data = :d WHERE id = :i"),
                          {"d": encrypt(decrypt(r.data, str(r.id).encode()), str(r.id).encode()), "i": r.id})
                n["documents"] += 1
        for r in c.execute(text("SELECT id, config FROM notification_channels")).all():
            cfg = r.config
            blob = base64.b64decode(cfg["enc1"]) if isinstance(cfg, dict) and set(cfg) == {"enc1"} else b""
            if not blob or needs_rewrap(blob):
                c.execute(text("UPDATE notification_channels SET config = CAST(:c AS jsonb) WHERE id = :i"),
                          {"c": json.dumps(encrypt_json(decrypt_json(cfg, str(r.id)), str(r.id))), "i": r.id})
                n["notification_channels"] += 1
        for r in c.execute(text("SELECT id, url FROM calendar_feeds WHERE url IS NOT NULL")).all():
            blob = _text_blob(r.url) if r.url.startswith(PREFIX) else b""
            if not blob or needs_rewrap(blob):
                c.execute(text("UPDATE calendar_feeds SET url = :u WHERE id = :i"),
                          {"u": encrypt_text(decrypt_text(r.url, str(r.id)), str(r.id)), "i": r.id})
                n["calendar_feeds"] += 1
    return n


def status() -> dict:
    from sqlalchemy import create_engine, text
    from app.config import get_settings
    eng = create_engine(get_settings().owner_db_url)
    with eng.connect() as c:
        docs = c.execute(text("SELECT data FROM documents")).all()
        chans = c.execute(text("SELECT config FROM notification_channels")).all()
        cals = c.execute(text("SELECT url FROM calendar_feeds WHERE url IS NOT NULL")).all()
    return {"key_set": enabled(),
            "documents": {"encrypted": sum(is_encrypted(r.data) for r in docs), "total": len(docs)},
            "notification_channels": {"encrypted": sum(isinstance(r.config, dict) and set(r.config) == {"enc1"} for r in chans), "total": len(chans)},
            "calendar_links": {"encrypted": sum(r.url.startswith(PREFIX) for r in cals), "total": len(cals)}}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "new-key":
        print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="))
    elif cmd == "rewrap":
        print(rewrap())
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    else:
        print(__doc__)
        sys.exit(2)
