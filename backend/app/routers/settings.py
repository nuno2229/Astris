"""Household settings. Local LLM endpoint (llama.cpp, OpenAI-compatible API) is configured here,
owner only, because it decides where household data is sent (D-2, §2)."""
import json
import urllib.error
import urllib.request
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.deps import DB, User
from app.netcheck import is_blocked_url, is_local_url
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["settings"])


class LlmSettings(BaseModel):
    llm_enabled: bool = False
    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_model: str | None = Field(default=None, max_length=200)
    llm_allow_internet: bool = False

    @field_validator("llm_base_url")
    @classmethod
    def check_url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("Must start with http:// or https://")
        return v


class SettingsOut(LlmSettings):
    llm_is_local: bool | None
    can_edit: bool


class LlmTestIn(BaseModel):
    llm_base_url: str = Field(max_length=300)
    llm_allow_internet: bool = False


class LlmTestOut(BaseModel):
    ok: bool
    message: str
    models: list[str] = []


def _require_owner(db: DB, hid: UUID) -> None:
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change these settings")


def _check_privacy(url: str | None, allow_internet: bool) -> None:
    if url and not allow_internet and not is_local_url(url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "This address is outside your home network, so household data would leave home. "
                            "Use a local address, or explicitly allow internet use.")


@router.get("/households/{hid}/settings", response_model=SettingsOut)
def get_settings_(hid: UUID, user: User, db: DB):
    role = my_role(db, hid)
    row = db.execute(text("""SELECT llm_enabled, llm_base_url, llm_model, llm_allow_internet
                               FROM household_settings WHERE household_id = :h"""), {"h": hid}).first()
    s = LlmSettings(**row._mapping) if row else LlmSettings()
    return SettingsOut(**s.model_dump(), can_edit=role == "owner",
                       llm_is_local=is_local_url(s.llm_base_url) if s.llm_base_url else None)


@router.put("/households/{hid}/settings", response_model=SettingsOut)
def put_settings(hid: UUID, body: LlmSettings, user: User, db: DB):
    _require_owner(db, hid)
    if body.llm_enabled and not body.llm_base_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Set the server address before enabling AI")
    _check_privacy(body.llm_base_url, body.llm_allow_internet)
    if body.llm_base_url and is_blocked_url(body.llm_base_url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This address can't be used")
    db.execute(text("""
        INSERT INTO household_settings (household_id, llm_enabled, llm_base_url, llm_model, llm_allow_internet, updated_by)
        VALUES (:h, :e, :u, :m, :ai, :by)
        ON CONFLICT (household_id) DO UPDATE SET llm_enabled = EXCLUDED.llm_enabled, llm_base_url = EXCLUDED.llm_base_url,
            llm_model = EXCLUDED.llm_model, llm_allow_internet = EXCLUDED.llm_allow_internet,
            updated_by = EXCLUDED.updated_by, updated_at = now()"""),
        {"h": hid, "e": body.llm_enabled, "u": body.llm_base_url, "m": (body.llm_model or "").strip() or None,
         "ai": body.llm_allow_internet, "by": user.id})
    return get_settings_(hid, user, db)


_FAIL = "Couldn't connect to a llama.cpp server at this address. Check the address and port."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@router.post("/households/{hid}/settings/llm/test", response_model=LlmTestOut)
def test_llm(hid: UUID, body: LlmTestIn, user: User, db: DB):
    """Calls GET {base}/v1/models (llama.cpp server's OpenAI-compatible API). Sends no household data."""
    _require_owner(db, hid)
    url = LlmSettings.check_url(body.llm_base_url)
    if not url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the server address")
    _check_privacy(url, body.llm_allow_internet)
    if is_blocked_url(url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This address can't be used")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url + "/v1/models", headers={"Accept": "application/json"}),
                         timeout=5) as r:
            data = json.loads(r.read(256 * 1024))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # one generic message on purpose: detailed errors would reveal which hosts/ports exist
        return LlmTestOut(ok=False, message=_FAIL)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return LlmTestOut(ok=False, message=_FAIL)
    models = [str(m.get("id", ""))[:200] for m in data["data"][:100] if isinstance(m, dict)]
    return LlmTestOut(ok=True, message=f"Connected. {len(models)} model(s) available.", models=[m for m in models if m])
