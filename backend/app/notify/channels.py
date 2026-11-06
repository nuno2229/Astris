"""Notification senders (E22). Destinations are strict so this can't become a general HTTP client:
Discord -> https://discord.com/api/webhooks/<id>/<token> only; Telegram -> api.telegram.org only;
ntfy -> scheme://host[:port] + topic, through the pinned client with the home-network rule."""
import json
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from app.netcheck import is_local_url, is_self_or_loopback, origin_only, pinned_request, resolve

DISCORD_RE = re.compile(r"^https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)"
                        r"/api/webhooks/(\d{5,25})/([A-Za-z0-9_\-]{20,120})/?$")
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_\-]{30,60}$")
TELEGRAM_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")
NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


ALLOW_LOOPBACK = False   # ntfy may not target the app's own container (tests flip this)
FAIL = "Couldn't deliver the message. Check the details and that the service is reachable."


class ChannelError(ValueError):
    """Message is safe to show the user."""


@dataclass
class Clean:
    config: dict
    uses_internet: bool
    hint: str           # masked description for the UI (never the secret)


def validate(kind: str, cfg: dict, allow_internet: bool) -> Clean:
    if kind == "discord":
        m = DISCORD_RE.match(str(cfg.get("webhook_url", "")).strip())
        if not m:
            raise ChannelError("Paste a Discord webhook URL (https://discord.com/api/webhooks/…)")
        return Clean({"webhook_url": f"https://discord.com/api/webhooks/{m[1]}/{m[2]}"}, True,
                     f"Discord webhook …{m[1][-4:]}")
    if kind == "telegram":
        tok, chat = str(cfg.get("bot_token", "")).strip(), str(cfg.get("chat_id", "")).strip()
        if not TELEGRAM_TOKEN_RE.match(tok):
            raise ChannelError("That doesn't look like a Telegram bot token (from @BotFather)")
        if not TELEGRAM_CHAT_RE.match(chat):
            raise ChannelError("Chat ID should be a number (or @channelname)")
        return Clean({"bot_token": tok, "chat_id": chat}, True, f"Telegram chat {chat[:3]}…{chat[-2:]}")
    if kind == "ntfy":
        server = origin_only(str(cfg.get("server", "")).strip() or "https://ntfy.sh")
        topic = str(cfg.get("topic", "")).strip()
        token = str(cfg.get("token", "") or "").strip()
        if server is None:
            raise ChannelError("ntfy server should be just the address, like http://192.168.1.10:8090")
        if not NTFY_TOPIC_RE.match(topic):
            raise ChannelError("Topic can use letters, numbers, - and _ (up to 64)")
        if token and not re.match(r"^[A-Za-z0-9_\-.]{8,200}$", token):
            raise ChannelError("Access token has unexpected characters")
        host = urlsplit(server).hostname or ""
        if not ALLOW_LOOPBACK and (host in ("localhost", "ip6-localhost") or any(
                is_self_or_loopback(a) for a in resolve(host))):  # early, friendly check; enforced again at connect
            raise ChannelError("This address can't be used. Use your server's network address instead.")
        internet = not is_local_url(server)
        if internet and not allow_internet:
            raise ChannelError("This ntfy server is outside your home network. Allow internet use to save it.")
        return Clean({"server": server, "topic": topic, "token": token or None}, internet,
                     f"ntfy {urlsplit(server).hostname}/{topic[:3]}…")
    raise ChannelError("Unknown channel type")


def send(kind: str, cfg: dict, title: str, body: str, timeout: float = 15) -> None:
    """Raises ChannelError with a generic message on failure (never echoes secrets)."""
    if kind == "discord":
        url = cfg["webhook_url"]
        if not DISCORD_RE.match(url):
            raise ChannelError("Stored Discord address is invalid")
        payload = {"content": f"**{title}**\n{body}"[:1900],
                   "allowed_mentions": {"parse": []}}  # names from statements can never ping @everyone
        req = (url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    elif kind == "telegram":
        if not TELEGRAM_TOKEN_RE.match(cfg["bot_token"]):
            raise ChannelError("Stored Telegram token is invalid")
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        payload = {"chat_id": cfg["chat_id"], "text": f"{title}\n{body}"[:3900], "disable_web_page_preview": True}
        req = (url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    elif kind == "ntfy":
        server = origin_only(cfg["server"])
        if server is None or not NTFY_TOPIC_RE.match(cfg["topic"]):
            raise ChannelError("Stored ntfy settings are invalid")
        headers = {"Title": title.encode("ascii", "ignore").decode()[:100], "Tags": "star"}
        if cfg.get("token"):
            headers["Authorization"] = f"Bearer {cfg['token']}"
        req = (f"{server}/{quote(cfg['topic'])}", body[:3900].encode(), headers)
    else:
        raise ChannelError("Unknown channel type")
    try:
        code, _ = pinned_request(req[0], "POST", req[1], timeout=timeout, max_bytes=16 * 1024, headers=req[2],
                                 block_self=(kind == "ntfy" and not ALLOW_LOOPBACK))
    except Exception:
        raise ChannelError(FAIL)  # one generic message: details would reveal which hosts/ports exist
    if code >= 300:
        raise ChannelError(FAIL)
