"""Kept originals (opt-in): receipt photos and statements. Stored in the database so RLS and backups cover them."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import DB, User, not_found

router = APIRouter(prefix="/api", tags=["documents"])
USER_QUOTA = 1024 * 1024 * 1024        # 1 GB of kept originals per person

_SNIFF = [(b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"),
          (b"%PDF", "application/pdf")]


def content_type(raw: bytes, filename: str) -> str:
    """Decide the type from the bytes, never from what the browser claimed."""
    head = raw[:16]
    for magic, ct in _SNIFF:
        if head.startswith(magic):
            return ct
    if head[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if filename.lower().endswith(".csv"):
        return "text/csv"
    try:
        raw[:4096].decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def keep(db: Session, user_id: UUID, kind: str, filename: str, raw: bytes, *, household_id=None, account_id=None,
         receipt_id=None, import_batch_id=None) -> UUID:
    used = db.execute(text("SELECT COALESCE(sum(size), 0) FROM documents WHERE owner_user_id = :u"), {"u": user_id}).scalar()
    if used + len(raw) > USER_QUOTA:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            "You've reached the storage limit for kept originals (1 GB). Delete some first.")
    return db.execute(text("""
        INSERT INTO documents (kind, owner_user_id, household_id, account_id, receipt_id, import_batch_id, filename,
                               content_type, size, data)
        VALUES (:k, :u, :h, :a, :r, :b, :f, :ct, :s, :d) RETURNING id"""),
        {"k": kind, "u": user_id, "h": household_id, "a": account_id, "r": receipt_id, "b": import_batch_id,
         "f": (filename or "upload")[:255], "ct": content_type(raw, filename or ""), "s": len(raw), "d": raw}).scalar_one()


@router.get("/documents/{did}")
def download(did: UUID, user: User, db: DB):
    r = db.execute(text("SELECT filename, content_type, data FROM documents WHERE id = :d"), {"d": did}).first()
    if r is None:
        raise not_found("File not found")
    safe = "".join(ch for ch in r.filename if ch.isalnum() or ch in "._- ")[:100] or "file"
    # always a download, never rendered inline: an uploaded file can't run script in the app's origin
    return Response(bytes(r.data), media_type=r.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{safe}"', "Content-Security-Policy": "sandbox",
                             "Cache-Control": "private, no-store"})


@router.delete("/documents/{did}", status_code=204)
def delete(did: UUID, user: User, db: DB):
    if not db.execute(text("DELETE FROM documents WHERE id = :d"), {"d": did}).rowcount:
        raise not_found("File not found")


@router.get("/accounts/{aid}/documents")
def account_documents(aid: UUID, user: User, db: DB):
    rows = db.execute(text("""SELECT d.id, d.filename, d.size, d.created_at FROM documents d
                               WHERE d.kind = 'statement' AND d.account_id = :a ORDER BY d.created_at DESC"""), {"a": aid})
    return [dict(r._mapping) for r in rows]
