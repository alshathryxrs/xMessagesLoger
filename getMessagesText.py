import asyncio
import base64
import logging
import json
import signal
import struct
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional

import httpx
import aiohttp
from playwright.async_api import async_playwright, BrowserContext, Page, Route

# ============================================================
# 1. CONFIGURATION
# ============================================================

@dataclass(frozen=True)
class AccountConfig:
    user_id:    str
    auth_token: str
    ct0:        str
    user_data:  str
    label:      str

ACCOUNT1 = AccountConfig(
    user_id    = "704772337",
    auth_token = "000109a238c22edaed3918aacb3d8c0a4360d480",
    ct0        = "6fd94ab6e318068f4e34c27c07d5b055541c8447ddc43e14d8ac0cedbe02a6efb5060c89092b47d6e071e61aca37496f44886a1c141abc20bb5aec817f214dcd2fbc7f22f39372ab5a8664ecb553f439",
    user_data  = "/tmp/browser_data_acc1",
    label      = "Acc1",
)
ACCOUNT2 = AccountConfig(
    user_id    = "2050569848002957312",
    auth_token = "52374ce131bffe2766c9878c792f5e0074a200a1",
    ct0        = "e0c6dab0995fa9426c647337b5b16678b75852d196a688526489efcfe924ed5cb288e87d5b43562e529f33af770670115413b85aa4f7f3b0f9edec945ab7f62811b4fafb8125de79722be7d8dc7e9e4c",
    user_data  = "/tmp/browser_data_acc2",
    label      = "Acc2",
)

BEARER_TOKEN   = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TELEGRAM_TOKEN   = "8630469503:AAGh-gLtUHBONPfT_1-z5oN41LCVnsWqqus"
TELEGRAM_CHAT_ID = 6607397366
PASSCODE         = "0807"
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0"
HEARTBEAT_B64    = "DAACDAACAAAA"
PRESENCE_TIMEOUT = 20

# Watched users
TARGET_USER_ID = "1885488902670000129"
REEM_USER_ID   = "954222428791681025"
NOORA_USER_ID  = "2082060317358743552"
JAMILA_USER_ID = "2024978767081254912"

WATCHED = {
    TARGET_USER_ID: "Target",
    REEM_USER_ID:   "Reem",
    NOORA_USER_ID:  "Noora",
    JAMILA_USER_ID: "Jamila",
}

# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("xML")
for lib in ("httpx", "httpcore", "hpack", "aiohttp"):
    logging.getLogger(lib).setLevel(logging.WARNING)

def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

# ============================================================
# 3. PRESENCE  (suppress seen/typing when YOU are active)
# ============================================================

_presence: Dict[str, asyncio.TimerHandle] = {}

def mark_presence(conv_id: str) -> None:
    loop = asyncio.get_event_loop()
    old  = _presence.get(conv_id)
    if old: old.cancel()
    def expire(): _presence.pop(conv_id, None)
    _presence[conv_id] = loop.call_later(PRESENCE_TIMEOUT, expire)

def is_muted(conv_id: str) -> bool:
    return conv_id in _presence

# ============================================================
# 4. TELEGRAM SERVICE
# ============================================================

class TelegramService:
    def __init__(self):
        self.base  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        self.offset = 0
        self._cli: Optional[httpx.AsyncClient] = None

    @property
    def cli(self) -> httpx.AsyncClient:
        if self._cli is None:
            self._cli = httpx.AsyncClient(timeout=40.0)
        return self._cli

    async def close(self):
        if self._cli:
            await self._cli.aclose()

    @property
    def menu(self) -> Dict[str, Any]:
        return {"inline_keyboard": [
            [
                {"text": "📬 Noora (A1)",   "callback_data": "check_noora_acc1"},
                {"text": "📸 Noora (A1)",   "callback_data": "screen_noora_acc1"},
            ],
            [
                {"text": "📬 Jamila (A1)",  "callback_data": "check_jamila_acc1"},
                {"text": "📸 Jamila (A1)",  "callback_data": "screen_jamila_acc1"},
            ],
            [
                {"text": "📬 Jamila (A2)",  "callback_data": "check_jamila_acc2"},
                {"text": "📸 Jamila (A2)",  "callback_data": "screen_jamila_acc2"},
            ],
            [{"text": "⚙️ Status", "callback_data": "status"}],
        ]}

    async def _post(self, ep: str, **kw) -> bool:
        url = f"{self.base}/{ep}"
        for attempt in range(1, 4):
            try:
                if "files" in kw:
                    r = await self.cli.post(url, data=kw["data"], files=kw["files"])
                else:
                    r = await self.cli.post(url, json=kw["json"])
                if r.is_success:
                    return True
            except Exception:
                pass
            if attempt < 3:
                await asyncio.sleep(2 * attempt)
        return False

    async def send_text(self, text: str, with_menu: bool = True) -> None:
        payload: Dict[str, Any] = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }
        if with_menu:
            payload["reply_markup"] = self.menu
        await self._post("sendMessage", json=payload)

    async def send_alert(self, title: str, body: str) -> None:
        """Fast alert — no menu, no formatting overhead."""
        await self._post("sendMessage", json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       f"*{title}*\n{body}",
            "parse_mode": "Markdown",
        })

    async def send_payload(self, data: Dict[str, Any], label: str) -> None:
        text   = (data.get("text") or "").strip()
        images = data.get("images", [])
        ts     = data.get("time") or datetime.now().strftime("%I:%M %p")

        if text:
            await self._post("sendMessage", json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       f"◆ *{label}*  ·  {ts}\n\n{text}\n\n· · · · · · · · · · ·",
                "parse_mode": "Markdown",
            })

        for b64 in images:
            if "," in b64:
                b64 = b64.split(",")[1]
            try:
                img_bytes = base64.b64decode(b64)
                await self._post(
                    "sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📸 {label} {ts}"},
                    files={"photo": ("msg.png", img_bytes, "image/png")},
                )
            except Exception:
                pass

    async def send_screenshot(self, img: bytes, caption: str) -> None:
        await self._post(
            "sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption,
                  "reply_markup": json.dumps(self.menu)},
            files={"photo": ("shot.png", img, "image/png")},
        )

    async def answer_callback(self, cb_id: str) -> None:
        await self._post("answerCallbackQuery", json={"callback_query_id": cb_id})

    async def poll(self) -> List[Dict[str, Any]]:
        try:
            r = await self.cli.get(
                f"{self.base}/getUpdates",
                params={"timeout": 30, "offset": self.offset,
                        "allowed_updates": ["message", "callback_query"]},
            )
            if not r.is_success:
                return []
            updates = r.json().get("result", [])
            if updates:
                self.offset = updates[-1]["update_id"] + 1
            return updates
        except Exception:
            return []

# ============================================================
# 5. BROWSER ENGINE
# ============================================================

SPOOF_JS = """
Object.defineProperty(document, 'visibilityState', { get: () => 'hidden' });
Object.defineProperty(document, 'hidden',          { get: () => true });
Object.defineProperty(Document.prototype, 'hasFocus', { value: () => false });
window.addEventListener('visibilitychange', e => e.stopImmediatePropagation(), true);
window.addEventListener('focus',            e => e.stopImmediatePropagation(), true);
window.processedMap = new Map();
const _origWSSend = WebSocket.prototype.send;
WebSocket.prototype.send = function(data) {
    if (typeof data === 'string') return _origWSSend.call(this, data);
    return;
};
"""

EXTRACTOR_JS = """
async () => {
    async function blobToBase64(url) {
        try {
            const r = await fetch(url);
            const blob = await r.blob();
            return new Promise(res => {
                const fr = new FileReader();
                fr.onloadend = () => res(fr.result);
                fr.readAsDataURL(blob);
            });
        } catch(e) { return null; }
    }

    function extractText(el) {
        let text = '';
        function walk(node) {
            if (!node) return;
            if (node.nodeType === Node.TEXT_NODE) {
                const v = node.textContent || '';
                if (!/^(\\d{1,2}:\\d{2})(\\s*[APap][Mm])?$/.test(v.trim())) text += v;
                return;
            }
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const tag    = node.tagName.toLowerCase();
            const testId = (node.getAttribute('data-testid') || '').toLowerCase();
            if (tag === 'time' || testId.includes('time') || testId.includes('status')) return;
            if (tag === 'img') {
                const alt = node.getAttribute('alt') || '';
                const src = node.getAttribute('src') || '';
                if (alt && (src.includes('emoji') || src.includes('twemoji'))) text += alt;
                return;
            }
            for (const child of node.childNodes) walk(child);
        }
        walk(el);
        return text.trim();
    }

    const all = document.querySelectorAll('[data-testid^="message-"]');
    const results = [];

    for (const container of all) {
        const testId = container.getAttribute('data-testid');
        if (!/^message-[0-9a-f]{8}-/.test(testId)) continue;

        if (container.classList.contains('justify-end')) {
            if (!window.processedMap.has(testId)) window.processedMap.set(testId, {t:'OUT',i:999});
            continue;
        }

        const timeEl   = container.querySelector('time');
        const timeText = timeEl ? timeEl.innerText.trim() : null;
        const textEl   = container.querySelector('[data-testid^="message-text-"]');

        let cleanText = textEl ? extractText(textEl) : '';
        if (!cleanText) {
            const emojiSpan = container.querySelector('span[style*="font-size: 64px"]');
            if (emojiSpan) cleanText = emojiSpan.textContent.trim();
        }

        const imgEls     = container.querySelectorAll('img[src^="blob:"], img[src*="/media/"]');
        const base64Imgs = [];
        for (const img of imgEls) {
            if (img.src.includes('emoji') || img.src.includes('twemoji') || img.src.includes('profile_images')) continue;
            const b64 = await blobToBase64(img.src);
            if (b64) base64Imgs.push(b64);
        }

        const prev        = window.processedMap.get(testId) || {t:'',i:0};
        const textToSend  = (cleanText && cleanText !== prev.t) ? cleanText : '';
        const imagesToSend = base64Imgs.slice(prev.i);

        if (!textToSend && imagesToSend.length === 0) continue;

        window.processedMap.set(testId, {
            t: cleanText || prev.t,
            i: Math.max(prev.i, base64Imgs.length),
        });

        results.push({ text: textToSend, images: imagesToSend, time: timeText });
    }
    return results;
}
"""

class XChatEngine:
    def __init__(self, account: AccountConfig, pw):
        self.account = account
        self.pw      = pw
        self.ctx: Optional[BrowserContext] = None
        self.pages: Dict[str, Page]        = {}
        self.ready   = False

    async def _block_read_receipts(self, route: Route) -> None:
        keywords = ["mark_read", "markread", "dmconversationmarkread", "mark-read"]
        url = route.request.url.lower()
        if any(k in url for k in keywords):
            return await route.abort()
        try:
            pd = route.request.post_data or ""
            if route.request.method in ("POST", "PUT") and any(k in pd.lower() for k in keywords):
                return await route.abort()
        except Exception:
            pass
        await route.continue_()

    async def start(self) -> None:
        logger.info(f"🚀 [{self.account.label}] Starting browser...")
        self.ctx = await self.pw.chromium.launch_persistent_context(
            user_data_dir=self.account.user_data,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--window-size=1280,800",
                "--log-level=3",
            ],
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        if self.ctx.pages:
            await self.ctx.pages[0].close()
        await self.ctx.add_cookies([
            {"name": "auth_token", "value": self.account.auth_token, "domain": ".x.com", "path": "/"},
            {"name": "ct0",        "value": self.account.ct0,        "domain": ".x.com", "path": "/"},
        ])

    async def open_tab(self, name: str, target_uid: str) -> None:
        page = await self.ctx.new_page()
        page.on("console", lambda _: None)
        await page.route("**/*", self._block_read_receipts)
        await page.add_init_script(SPOOF_JS)

        url = f"https://x.com/i/chat/{self.account.user_id}-{target_uid}"
        await page.goto(url, wait_until="load", timeout=90000)

        try:
            await page.wait_for_selector("input[inputmode='numeric']", timeout=12000)
            for digit in PASSCODE:
                await page.keyboard.type(digit)
                await asyncio.sleep(0.15)
            await page.keyboard.press("Enter")
        except Exception:
            pass

        await page.wait_for_selector("[data-testid='dm-composer-textarea']", timeout=60000)
        self.pages[name] = page
        logger.info(f"✅ [{self.account.label}] Tab ready: {name}")

    async def get_messages(self, tab: str) -> List[Dict[str, Any]]:
        page = self.pages.get(tab)
        if not page:
            return []
        return await page.evaluate(EXTRACTOR_JS)

    async def screenshot(self, tab: str) -> bytes:
        page = self.pages.get(tab)
        if not page:
            raise Exception("Tab not ready")
        return await page.screenshot(full_page=False)

# ============================================================
# 6. THRIFT PARSER + FRAME PARSERS
# ============================================================

def parse_thrift(buf: bytes, offset: int):
    fields = {}
    while offset < len(buf):
        if offset + 3 > len(buf): break
        type_id = buf[offset]
        field   = struct.unpack_from(">H", buf, offset + 1)[0]
        offset += 3
        if type_id == 0: break
        if type_id == 11:
            if offset + 4 > len(buf): break
            ln = struct.unpack_from(">I", buf, offset)[0]; offset += 4
            if offset + ln > len(buf): break
            fields[field] = buf[offset:offset+ln].decode("utf-8", errors="replace"); offset += ln
        elif type_id == 12:
            inner, offset = parse_thrift(buf, offset); fields[field] = inner
        elif type_id == 15:
            if offset + 5 > len(buf): break
            et = buf[offset]; offset += 1
            cnt = struct.unpack_from(">I", buf, offset)[0]; offset += 4
            lst = []
            for _ in range(cnt):
                if et == 12:
                    inner, offset = parse_thrift(buf, offset); lst.append(inner)
                elif et == 11:
                    if offset + 4 > len(buf): break
                    ln = struct.unpack_from(">I", buf, offset)[0]; offset += 4
                    if offset + ln > len(buf): break
                    lst.append(buf[offset:offset+ln].decode("utf-8", errors="replace")); offset += ln
                else: break
            fields[field] = lst
        elif type_id == 10:
            if offset + 8 > len(buf): break
            hi, lo = struct.unpack_from(">II", buf, offset)
            fields[field] = hi * 4_294_967_296 + lo; offset += 8
        elif type_id == 8:
            if offset + 4 > len(buf): break
            fields[field] = struct.unpack_from(">i", buf, offset)[0]; offset += 4
        elif type_id == 2:
            if offset >= len(buf): break
            fields[field] = buf[offset]; offset += 1
        else: break
    return fields, offset

def try_parse_seen(buf):
    try:
        root, _ = parse_thrift(buf, 0)
        outer = root.get(1)
        if not outer: return None
        rid = str(outer.get(3, ""))
        if not rid.isdigit() or not (6 <= len(rid) <= 20): return None
        cid = str(outer.get(4, ""))
        if ":" not in cid: return None
        f7 = outer.get(7)
        if not f7: return None
        f12 = f7.get(12)
        if not f12 or not f12.get(1) or not f12.get(2): return None
        return {"reader_id": rid, "conv_id": cid}
    except: return None

def try_parse_message(buf):
    try:
        root, _ = parse_thrift(buf, 0)
        outer = root.get(1)
        if not outer: return None
        sid = str(outer.get(3, ""))
        if not sid.isdigit() or not (6 <= len(sid) <= 20): return None
        cid = str(outer.get(4, ""))
        if ":" not in cid: return None
        f7 = outer.get(7)
        if not f7: return None
        f1 = f7.get(1)
        if not f1 or f1.get(102) != 1: return None
        mid = outer.get(1)
        if not mid or not f1.get(104): return None
        return {"sender_id": sid, "conv_id": cid, "msg_id": str(mid)}
    except: return None

# ============================================================
# 7. TYPING STATE
# ============================================================

_typing_flags:  Dict[str, bool]                    = {}
_typing_timers: Dict[str, asyncio.TimerHandle]     = {}

async def on_typing(label: str, key: str, acct_label: str, tg: TelegramService) -> None:
    loop = asyncio.get_event_loop()
    if not _typing_flags.get(key):
        _typing_flags[key] = True
        if not is_muted(key):
            asyncio.create_task(tg.send_alert(f"{label} {acct_label} ⌨️", f"{label} is typing..."))
    old = _typing_timers.get(key)
    if old: old.cancel()
    def stop(): _typing_flags[key] = False
    _typing_timers[key] = loop.call_later(4.0, stop)

# ============================================================
# 8. WEBSOCKET FRAME HANDLER
# ============================================================

async def handle_frame(buf: bytes, account: AccountConfig, tg: TelegramService) -> None:
    lbl   = account.label
    my_id = account.user_id

    seen = try_parse_seen(buf)
    if seen:
        if seen["reader_id"] == my_id:
            mark_presence(seen["conv_id"])
            return
        if is_muted(seen["conv_id"]): return
        name = WATCHED.get(seen["reader_id"], "Someone")
        logger.info(f"👁️  [{lbl}] {name} SEEN")
        asyncio.create_task(tg.send_alert(f"{name} {lbl} 👁️", f"{name} has seen your message!"))
        return

    msg = try_parse_message(buf)
    if msg:
        sid = msg["sender_id"]
        if sid == my_id:
            mark_presence(msg["conv_id"])
            return
        if my_id not in msg["conv_id"].split(":"): return
        if is_muted(msg["conv_id"]): return
        name = WATCHED.get(sid, "Someone")
        logger.info(f"💬 [{lbl}] {name} sent a message")
        asyncio.create_task(tg.send_alert(f"{name} {lbl} 💬", f"{name} sent you a message!"))
        return

    try:
        cleaned  = "".join(c if 0x20 <= ord(c) <= 0x7E else " "
                           for c in buf.decode("utf-8", errors="replace")).strip()
        typer_id = cleaned.split()[1] if len(cleaned.split()) > 1 else ""
    except: return

    if not typer_id or not typer_id.isdigit() or not (6 <= len(typer_id) <= 20): return
    if typer_id == my_id:
        mark_presence(typer_id)
        return
    if is_muted(typer_id): return
    await on_typing(WATCHED.get(typer_id, "Someone"), typer_id, lbl, tg)

# ============================================================
# 9. WEBSOCKET MONITOR
# ============================================================

async def monitor(account: AccountConfig, tg: TelegramService) -> None:
    tag     = f"[{account.label}]"
    attempt = 0
    while True:
        attempt += 1
        backoff = min(2 ** (attempt - 1), 60)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    "https://api.x.com/graphql/Qh3fZRjPPtPoHYR_2sCZsA/GenerateXChatTokenMutation",
                    headers={
                        "User-Agent":    USER_AGENT,
                        "Accept":        "application/json",
                        "Content-Type":  "application/json",
                        "x-csrf-token":  account.ct0,
                        "authorization": BEARER_TOKEN,
                        "Cookie":        f"auth_token={account.auth_token}; ct0={account.ct0};",
                        "Origin":        "https://x.com",
                        "Referer":       "https://x.com/",
                    },
                    content=json.dumps({"variables": {}}).encode(),
                )
            data  = r.json()
            token = (
                (data.get("data") or {}).get("user_get_x_chat_auth_token", {}).get("token")
                or data.get("user_get_x_chat_auth_token", {}).get("token")
            )
            if not token: raise RuntimeError("No token")
            ws_url = f"wss://chat-ws.x.com/ws?token={token}"

            connector = aiohttp.TCPConnector(force_close=False, enable_cleanup_closed=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.ws_connect(
                    ws_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Origin":     "https://x.com",
                        "Cookie":     f"auth_token={account.auth_token}; ct0={account.ct0};",
                    },
                    receive_timeout=90,
                    autoclose=True,
                    autoping=True,
                ) as ws:
                    logger.info(f"🟢 {tag} WS CONNECTED")
                    await asyncio.sleep(1)
                    attempt = 0
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.BINARY:
                            if base64.b64encode(m.data).decode() == HEARTBEAT_B64:
                                continue
                            await handle_frame(m.data, account, tg)
                        elif m.type == aiohttp.WSMsgType.TEXT:
                            await handle_frame(m.data.encode(), account, tg)
                        elif m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                            logger.warning(f"🔴 {tag} WS closed — retry in {backoff}s")
                            break
                        elif m.type == aiohttp.WSMsgType.ERROR:
                            logger.warning(f"❌ {tag} WS error — retry in {backoff}s")
                            break
        except asyncio.TimeoutError:
            logger.warning(f"⏱ {tag} timeout — retry in {backoff}s")
        except Exception as e:
            logger.warning(f"❌ {tag} {type(e).__name__}: {e} — retry in {backoff}s")
        await asyncio.sleep(backoff)

# ============================================================
# 10. BOT ORCHESTRATOR
# ============================================================

class BotOrchestrator:
    def __init__(self):
        self.tg      = TelegramService()
        self.engines: Dict[str, XChatEngine] = {}
        self.ready   = False

    async def start_engines(self, pw) -> None:
        e1 = XChatEngine(ACCOUNT1, pw)
        await e1.start()
        await e1.open_tab("noora_acc1",   NOORA_USER_ID)
        await e1.open_tab("jamila_acc1",  JAMILA_USER_ID)
        e1.ready = True
        self.engines["acc1"] = e1

        await asyncio.sleep(5)

        e2 = XChatEngine(ACCOUNT2, pw)
        await e2.start()
        await e2.open_tab("jamila_acc2",  JAMILA_USER_ID)
        e2.ready = True
        self.engines["acc2"] = e2

        self.ready = True
        logger.info("✅ All engines ready")
        await self.tg.send_text("🤖 *X-Logger Online*\n\nGhost mode active on all tabs.")

    async def handle_command(self, command: str) -> None:
        if not self.ready:
            await self.tg.send_text("⏳ *Still booting…*", with_menu=False)
            return

        # /check_<name>_<acct>  or  /check_custom_<uid>_<acct>
        if command.startswith("/check_"):
            raw    = command[7:]
            parts  = raw.rsplit("_", 1)
            tab    = raw if len(parts) < 2 else raw
            acct   = parts[-1] if parts[-1] in ("acc1", "acc2") else "acc1"
            engine = self.engines.get(acct)
            if not engine:
                await self.tg.send_text(f"❌ No engine: {acct}")
                return
            # tab key = everything before last _accX
            tab_key = raw[: -(len(acct) + 1)] + "_" + acct
            label   = tab_key.replace("_", " ").title()
            logger.info(f"Fetching {label}…")
            msgs = await engine.get_messages(tab_key)
            if not msgs:
                await self.tg.send_text(f"📭 *No new from {label}*")
            else:
                for m in msgs:
                    await self.tg.send_payload(m, label)
                await self.tg.send_text(f"✅ *Done — {label}*")

        elif command.startswith("/screen_"):
            raw    = command[8:]
            parts  = raw.rsplit("_", 1)
            acct   = parts[-1] if parts[-1] in ("acc1", "acc2") else "acc1"
            engine = self.engines.get(acct)
            tab_key = raw[: -(len(acct) + 1)] + "_" + acct
            label   = tab_key.replace("_", " ").title()
            try:
                img = await engine.screenshot(tab_key)
                await self.tg.send_screenshot(img, f"📸 {label}")
            except Exception as e:
                await self.tg.send_text(f"❌ Screenshot failed: {e}")

        elif command.startswith("/open "):
            # /open <user_id> [acc1|acc2]
            parts   = command[6:].strip().split()
            uid     = parts[0] if parts else ""
            acct    = parts[1] if len(parts) > 1 else "acc1"
            engine  = self.engines.get(acct)
            if not uid.isdigit():
                await self.tg.send_text("❌ Usage: `/open <user_id> [acc1|acc2]`", with_menu=False)
                return
            if not engine:
                await self.tg.send_text(f"❌ No engine: {acct}", with_menu=False)
                return
            tab_key = f"custom_{uid}_{acct}"
            if tab_key in engine.pages:
                await self.tg.send_text(f"✅ Already open: `{uid}` on {acct.upper()}", with_menu=False)
                return
            await self.tg.send_text(f"⏳ Opening `{uid}` on {acct.upper()}…", with_menu=False)
            try:
                await engine.open_tab(tab_key, uid)
                await self.tg.send_text(
                    f"✅ Ready: `{uid}` on {acct.upper()}\n"
                    f"Use `/check_{tab_key}` or `/screen_{tab_key}`",
                    with_menu=False,
                )
            except Exception as e:
                await self.tg.send_text(f"❌ Failed: {e}", with_menu=False)

        elif command in ("/status", "/start"):
            tabs = {k: list(e.pages.keys()) for k, e in self.engines.items()}
            await self.tg.send_text(f"🤖 *X-Logger Online*\n\nTabs: `{json.dumps(tabs)}`")

    async def run(self, shutdown: asyncio.Event) -> None:
        async with async_playwright() as pw:
            await self.start_engines(pw)
            try:
                while not shutdown.is_set():
                    for upd in await self.tg.poll():
                        command = ""
                        chat_id = None
                        cb_id   = None

                        if "callback_query" in upd:
                            cb      = upd["callback_query"]
                            cb_id   = cb["id"]
                            chat_id = cb.get("message", {}).get("chat", {}).get("id")
                            command = f"/{cb['data']}"
                        elif "message" in upd:
                            m       = upd["message"]
                            chat_id = m.get("chat", {}).get("id")
                            command = m.get("text", "").strip().lower()

                        if not command or chat_id != TELEGRAM_CHAT_ID:
                            continue
                        if cb_id:
                            await self.tg.answer_callback(cb_id)
                        asyncio.create_task(self.handle_command(command))

                    await asyncio.sleep(1)
            finally:
                await self.tg.close()

# ============================================================
# 11. ENTRY POINT
# ============================================================

async def main():
    shutdown = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: shutdown.set())
            except NotImplementedError:
                pass

    logger.info("🚀 Starting X-Messages Logger…")
    tg = TelegramService()
    bot = BotOrchestrator()
    bot.tg = tg

    await asyncio.gather(
        bot.run(shutdown),
        monitor(ACCOUNT1, tg),
        monitor(ACCOUNT2, tg),
        return_exceptions=True,
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
