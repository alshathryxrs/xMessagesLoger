import asyncio
import base64
import logging
import json
import signal
import sys
import struct
import aiohttp
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional

import httpx
from playwright.async_api import async_playwright, BrowserContext, Page, Route

# ==========================================
# 1. CONFIGURATION
# ==========================================
@dataclass(frozen=True)
class AccountConfig:
    user_id:    str
    auth_token: str
    ct0:        str
    user_data:  str
    label:      str

ACCOUNT2 = AccountConfig(
    user_id    = "2050569848002957312",
    auth_token = "52374ce131bffe2766c9878c792f5e0074a200a1",
    ct0        = "e0c6dab0995fa9426c647337b5b16678b75852d196a688526489efcfe924ed5cb288e87d5b43562e529f33af770670115413b85aa4f7f3b0f9edec945ab7f62811b4fafb8125de79722be7d8dc7e9e4c",
    user_data  = "./browser_data_acc2",
    label      = "2",
)

TARGET_USER_ID = "1885488902670000129"
REEM_USER_ID   = "954222428791681025"
NOORA_USER_ID  = "2082060317358743552"
JAMILA_USER_ID = "2024978767081254912"

TELEGRAM_TOKEN   = "8728595372:AAFZGonnAbN_U40LjNrqRoQARycpqP7P0Hc"
TELEGRAM_CHAT_ID = 6607397366
PASSCODE         = "0807"
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0"

BEARER_TOKEN     = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
HEARTBEAT_B64    = "DAACDAACAAAA"

# ==========================================
# 2. LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("XWatcher")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ==========================================
# 3. TELEGRAM SERVICE
# ==========================================
class TelegramService:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        self.offset   = 0
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=40.0)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()

    @property
    def menu(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "📬 Jamila 2",  "callback_data": "check_jamila_acc2"},
                    {"text": "📸 Jamila 2",  "callback_data": "screen_jamila_acc2"},
                ],
                [
                    {"text": "⚙️ Status", "callback_data": "status"},
                ],
            ]
        }

    async def _post(self, endpoint: str, **kwargs) -> bool:
        url = f"{self.base_url}/{endpoint}"
        for attempt in range(1, 4):
            try:
                if "files" in kwargs:
                    resp = await self.client.post(url, data=kwargs["data"], files=kwargs["files"])
                else:
                    resp = await self.client.post(url, json=kwargs["json"])
                if resp.is_success:
                    return True
                logger.warning(f"Telegram {endpoint} HTTP {resp.status_code}: {resp.text[:100]}")
            except Exception as e:
                logger.warning(f"Telegram {endpoint} attempt {attempt} failed: {e}")
            if attempt < 3:
                await asyncio.sleep(2 * attempt)
        return False

    async def send_text(self, text: str, with_menu: bool = True) -> None:
        payload: Dict[str, Any] = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "Markdown",
        }
        if with_menu:
            payload["reply_markup"] = self.menu
        await self._post("sendMessage", json=payload)

    async def send_payload(self, data: Dict[str, Any], label: str) -> None:
        text   = (data.get("text") or "").strip()
        images = data.get("images", [])
        ts     = data.get("time") or datetime.now().strftime("%I:%M %p")
        name   = label.split(" ")[0].upper()

        if text:
            await self._post("sendMessage", json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text": (
                    f"◆ *{name}*  ·  {ts}\n"
                    f"\n"
                    f"{text}\n"
                    f"\n"
                    f"· · · · · · · · · · ·"
                ),
                "parse_mode": "Markdown",
            })

        for b64 in images:
            if "," in b64:
                b64 = b64.split(",")[1]
            try:
                img_bytes = base64.b64decode(b64)
                await self._post(
                    "sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📸 {name} {ts}"},
                    files={"photo": ("msg.png", img_bytes, "image/png")},
                )
            except Exception:
                pass

    async def send_screenshot(self, image_bytes: bytes, caption: str) -> None:
        await self._post(
            "sendPhoto",
            data={
                "chat_id":      TELEGRAM_CHAT_ID,
                "caption":      caption,
                "reply_markup": json.dumps(self.menu),
            },
            files={"photo": ("screenshot.png", image_bytes, "image/png")},
        )

    async def poll(self) -> List[Dict[str, Any]]:
        try:
            r = await self.client.get(
                f"{self.base_url}/getUpdates",
                params={
                    "timeout":         5,
                    "offset":          self.offset,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=10.0,
            )
            if not r.is_success:
                logger.warning(f"Telegram poll HTTP {r.status_code}")
                return []
            updates = r.json().get("result", [])
            if updates:
                self.offset = updates[-1]["update_id"] + 1
            return updates
        except Exception as e:
            logger.warning(f"Telegram poll error: {e}")
            return []

    async def answer_callback(self, cb_id: str) -> None:
        await self._post("answerCallbackQuery", json={"callback_query_id": cb_id})

global_telegram = TelegramService()

# ==========================================
# 4. BROWSER ENGINE
# ==========================================
SPOOF_JS = """
try {
    Object.defineProperty(document, 'visibilityState', { get: () => 'hidden' });
    Object.defineProperty(document, 'hidden',          { get: () => true });
    Object.defineProperty(Document.prototype, 'hasFocus', { value: () => false });
    window.addEventListener('visibilitychange', e => e.stopImmediatePropagation(), true);
    window.addEventListener('focus',            e => e.stopImmediatePropagation(), true);
} catch(e) {}
window.processedMap = new Map();
const _origWSSend = WebSocket.prototype.send;
WebSocket.prototype.send = function(data) {
    try {
        if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
            const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
            // Drop only seen receipt frames: Thrift type=12(0x0C), field=1(0x00 0x01)
            if (bytes.length > 3 && bytes[0] === 0x0C && bytes[1] === 0x00 && bytes[2] === 0x01) {
                return;
            }
        }
    } catch(e) {}
    return _origWSSend.call(this, data);
};
"""

# Fix 2: parallel image fetch with Promise.all
# Fix 3: cursor-based scan — only process new messages since last check
EXTRACTOR_JS = """
async () => {
    async function blobToBase64(url) {
        try {
            const r    = await fetch(url);
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

    const all     = document.querySelectorAll('[data-testid^="message-"]');
    const results = [];

    for (const container of all) {
        const testId = container.getAttribute('data-testid');
        if (!/^message-[0-9a-f]{8}-/.test(testId)) continue;
        if (container.classList.contains('justify-end')) {
            if (!window.processedMap.has(testId)) window.processedMap.set(testId, { t: 'OUT', i: 999 });
            continue;
        }

        const timeEl   = container.querySelector('time');
        const timeText = timeEl ? timeEl.innerText.trim() : null;
        const textEl   = container.querySelector('[data-testid^="message-text-"]');
        let cleanText  = textEl ? extractText(textEl) : '';
        if (!cleanText) {
            const emojiSpan = container.querySelector('span[style*="font-size: 64px"]');
            if (emojiSpan) cleanText = emojiSpan.textContent.trim();
        }

        const prev    = window.processedMap.get(testId) || { t: '', i: 0 };
        const imgEls  = container.querySelectorAll('img[src^="blob:"], img[src*="/media/"]');
        const validImgs = Array.from(imgEls).filter(img =>
            !img.src.includes('emoji') &&
            !img.src.includes('twemoji') &&
            !img.src.includes('profile_images')
        );

        if (cleanText === prev.t && validImgs.length <= prev.i) continue;

        const textToSend = (cleanText && cleanText !== prev.t) ? cleanText : '';

        // Fix 2: fetch all new images in parallel
        const newImgUrls = validImgs.slice(prev.i).map(img => img.src);
        const b64Results = await Promise.all(newImgUrls.map(blobToBase64));
        const imagesToSend = b64Results.filter(Boolean);

        if (!textToSend && imagesToSend.length === 0) continue;

        window.processedMap.set(testId, {
            t: cleanText || prev.t,
            i: Math.max(prev.i, validImgs.length),
        });

        results.push({ text: textToSend, images: imagesToSend, time: timeText });
    }
    return results;
}
"""

class XChatEngine:
    def __init__(self, account: AccountConfig, playwright_instance):
        self.account  = account
        self.pw       = playwright_instance
        self.ctx: Optional[BrowserContext] = None
        self.pages: Dict[str, Page]        = {}
        self._tab_uids: Dict[str, str]     = {}  # name → target_uid for reconnect
        self.ready    = False

    async def _block_read_receipts(self, route: Route) -> None:
        keywords = ["mark_read", "markread", "dmconversationmarkread", "mark-read"]
        if any(k in route.request.url.lower() for k in keywords):
            return await route.abort()
        try:
            pd = route.request.post_data or ""
            if route.request.method in ("POST", "PUT") and any(k in pd.lower() for k in keywords):
                return await route.abort()
        except Exception:
            pass
        await route.continue_()

    async def _open_tab(self, name: str, target_uid: str) -> None:
        self._tab_uids[name] = target_uid
        page = await self.ctx.new_page()
        page.on("console", lambda _: None)
        await page.route("**/*", self._block_read_receipts)
        await page.add_init_script(SPOOF_JS)

        url = f"https://x.com/i/chat/{self.account.user_id}-{target_uid}"
        logger.info(f"🌐 [Acc{self.account.label}] Opening {url}")
        await page.goto(url, wait_until="load", timeout=90000)

        try:
            await page.wait_for_selector("input[inputmode='numeric']", timeout=12000)
            logger.info(f"🔑 [Acc{self.account.label}] PIN prompt — entering")
            for digit in PASSCODE:
                await page.keyboard.type(digit)
                await asyncio.sleep(0.15)
            await page.keyboard.press("Enter")
        except Exception:
            pass

        await page.wait_for_selector("[data-testid='dm-composer-textarea']", timeout=60000)
        await page.wait_for_selector("[data-testid^='message-']", timeout=30000)

        # Fix 6: reopen tab automatically if it crashes
        page.on("close", lambda: asyncio.create_task(self._on_page_close(name)))

        self.pages[name] = page
        logger.info(f"✅ [Acc{self.account.label}] {name} tab ready")

    # Fix 6: auto-reopen dead tab
    async def _on_page_close(self, name: str) -> None:
        logger.warning(f"⚠️  [Acc{self.account.label}] Tab '{name}' closed — reopening in 5s...")
        self.pages.pop(name, None)
        await asyncio.sleep(5)
        target_uid = self._tab_uids.get(name)
        if target_uid:
            try:
                await self._open_tab(name, target_uid)
                logger.info(f"✅ [Acc{self.account.label}] Tab '{name}' reopened")
            except Exception as e:
                logger.error(f"❌ [Acc{self.account.label}] Failed to reopen '{name}': {e}")

    async def start(self) -> None:
        logger.info(f"🚀 [Acc{self.account.label}] Starting browser...")
        self.ctx = await self.pw.chromium.launch_persistent_context(
            user_data_dir=self.account.user_data,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--disk-cache-size=0",
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

    async def get_messages(self, tab: str) -> List[Dict[str, Any]]:
        page = self.pages.get(tab)
        if not self.ready or not page:
            return []
        try:
            return await page.evaluate(EXTRACTOR_JS)
        except Exception as e:
            logger.error(f"❌ get_messages failed for {tab}: {e}")
            return []

    async def screenshot(self, tab: str) -> bytes:
        page = self.pages.get(tab)
        if not self.ready or not page:
            raise Exception("Tab not ready")
        try:
            return await page.screenshot(full_page=False)
        except Exception as e:
            raise Exception(f"Screenshot failed: {e}")

# ==========================================
# 5. WEBSOCKET WATCHER (Acc2 only)
# ==========================================
def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def get_label(sender_id: str) -> str:
    return {
        TARGET_USER_ID: "Target",
        REEM_USER_ID:   "Reem",
        NOORA_USER_ID:  "Noora",
        JAMILA_USER_ID: "Jamila",
    }.get(sender_id, "Someone")

async def fetch_ws_url(account: AccountConfig) -> str:
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
    if not r.is_success:
        raise RuntimeError(f"Token fetch failed: HTTP {r.status_code}")
    data  = r.json()
    token = (
        (data.get("data") or {}).get("user_get_x_chat_auth_token", {}).get("token")
        or data.get("user_get_x_chat_auth_token", {}).get("token")
    )
    if not token:
        raise RuntimeError("Token not found in response")
    return f"wss://chat-ws.x.com/ws?token={token}"

async def monitor(account: AccountConfig) -> None:
    tag     = f"[Acc{account.label}]"
    attempt = 0
    while True:
        attempt += 1
        backoff = min(2 ** (attempt - 1), 60)
        try:
            ws_url    = await fetch_ws_url(account)
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
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            buf = msg.data
                            if base64.b64encode(buf).decode() == HEARTBEAT_B64:
                                continue
                            # Fix 4: WS only logs, no Telegram noise
                            name = _try_get_sender_name(buf, account.user_id)
                            if name:
                                logger.info(f"💬 {tag} New message from {name}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                            logger.info(f"🔴 {tag} WS closed — reconnecting in {backoff}s")
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.info(f"❌ {tag} WS error — reconnecting in {backoff}s")
                            break
        except asyncio.TimeoutError:
            logger.info(f"⏱ {tag} Timeout — reconnecting in {backoff}s")
        except Exception as e:
            logger.info(f"❌ {tag} {type(e).__name__}: {e} — reconnecting in {backoff}s")
        await asyncio.sleep(backoff)

def _try_get_sender_name(buf: bytes, my_id: str) -> Optional[str]:
    """Returns sender name if this is an incoming message frame, else None."""
    try:
        fields, _ = _parse_thrift(buf, 0)
        outer = fields.get(1)
        if not outer: return None
        sid = str(outer.get(3, ""))
        if not sid.isdigit() or not (6 <= len(sid) <= 20): return None
        if sid == my_id: return None
        cid = str(outer.get(4, ""))
        if ":" not in cid: return None
        if my_id not in cid.split(":"): return None
        f7 = outer.get(7)
        if not f7: return None
        f1 = f7.get(1)
        if not f1 or f1.get(102) != 1: return None
        if not outer.get(1) or not f1.get(104): return None
        return get_label(sid)
    except Exception:
        return None

def _parse_thrift(buf: bytes, offset: int):
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
            inner, offset = _parse_thrift(buf, offset); fields[field] = inner
        elif type_id == 15:
            if offset + 5 > len(buf): break
            et = buf[offset]; offset += 1
            cnt = struct.unpack_from(">I", buf, offset)[0]; offset += 4
            lst = []
            for _ in range(cnt):
                if et == 12:
                    inner, offset = _parse_thrift(buf, offset); lst.append(inner)
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

# ==========================================
# 6. ORCHESTRATOR
# ==========================================
class BotOrchestrator:
    def __init__(self):
        self.telegram = global_telegram
        self.engines: Dict[str, XChatEngine] = {}
        self.ready    = False

    async def start_engines(self, pw) -> None:
        e2 = XChatEngine(ACCOUNT2, pw)
        await e2.start()
        await e2._open_tab("jamila_acc2", JAMILA_USER_ID)
        e2.ready = True
        self.engines["acc2"] = e2
        self.ready = True
        logger.info("✅ All engines ready — awaiting Telegram commands")
        await self.telegram.send_text("🤖 *X-Watcher Ready*\n\nGhost Mode active.")

    async def handle_command(self, command: str) -> None:
        if not self.ready:
            await self.telegram.send_text("⏳ *Still booting. Please wait.*", with_menu=False)
            return

        if command.startswith("/check_"):
            parts    = command[7:].split("_")
            name     = parts[0]
            acct     = parts[1] if len(parts) > 1 else "acc2"
            engine   = self.engines.get(acct)
            tab      = f"{name}_{acct}"
            acct_num = acct.replace("acc", "")
            label    = f"{name.capitalize()} {acct_num}"
            logger.info(f"📬 Fetching {label}...")

            if not engine:
                await self.telegram.send_text(f"❌ No engine for {acct}", with_menu=False)
                return

            msgs = await engine.get_messages(tab)
            if not msgs:
                await self.telegram.send_text(f"📭 *No new messages from {label}.*")
            else:
                for m in msgs:
                    await self.telegram.send_payload(m, label)
                await self.telegram.send_text(f"✅ *Done — {len(msgs)} message(s) from {label}.*")

        elif command.startswith("/screen_"):
            parts    = command[8:].split("_")
            name     = parts[0]
            acct     = parts[1] if len(parts) > 1 else "acc2"
            engine   = self.engines.get(acct)
            tab      = f"{name}_{acct}"
            acct_num = acct.replace("acc", "")
            label    = f"{name.capitalize()} {acct_num}"
            logger.info(f"📸 Screenshot {label}...")
            try:
                img = await engine.screenshot(tab)
                await self.telegram.send_screenshot(img, f"📸 {label}")
            except Exception as e:
                await self.telegram.send_text(f"❌ Screenshot failed: {e}", with_menu=False)

        elif command.startswith("/open "):
            parts   = command[6:].strip().split()
            user_id = parts[0] if parts else ""
            acct    = parts[1] if len(parts) > 1 else "acc2"
            engine  = self.engines.get(acct)

            if not user_id.isdigit():
                await self.telegram.send_text("❌ Usage: `/open <user_id> [acc2]`", with_menu=False)
                return
            if not engine:
                await self.telegram.send_text(f"❌ No engine for {acct}", with_menu=False)
                return

            tab_name = f"custom_{user_id}_{acct}"
            if tab_name in engine.pages:
                await self.telegram.send_text(f"✅ Tab already open for `{user_id}`", with_menu=False)
                return

            await self.telegram.send_text(f"⏳ Opening tab for `{user_id}`...", with_menu=False)
            try:
                await engine._open_tab(tab_name, user_id)
                await self.telegram.send_text(
                    f"✅ Tab ready for `{user_id}`\n"
                    f"Use `/check_{tab_name}` or `/screen_{tab_name}`",
                    with_menu=False,
                )
            except Exception as e:
                await self.telegram.send_text(f"❌ Failed: {e}", with_menu=False)

        elif command in ("/status", "/start"):
            tabs = {acct: list(e.pages.keys()) for acct, e in self.engines.items()}
            await self.telegram.send_text(
                f"🤖 *X-Watcher Online*\n\nOpen tabs: `{tabs}`",
            )

    # Fix 1: poll runs as a separate task, never blocks command handling
    async def _poll_loop(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            try:
                updates = await self.telegram.poll()
                for upd in updates:
                    command = ""
                    chat_id = None
                    cb_id   = None

                    if "callback_query" in upd:
                        cb      = upd["callback_query"]
                        cb_id   = cb["id"]
                        chat_id = cb.get("message", {}).get("chat", {}).get("id")
                        command = f"/{cb['data']}"
                    elif "message" in upd:
                        msg     = upd["message"]
                        chat_id = msg.get("chat", {}).get("id")
                        command = msg.get("text", "").strip().lower()

                    if not command or chat_id != TELEGRAM_CHAT_ID:
                        continue
                    if cb_id:
                        await self.telegram.answer_callback(cb_id)

                    logger.info(f"📥 Command: {command}")
                    asyncio.create_task(self.handle_command(command))

            except Exception as e:
                logger.warning(f"Poll loop error: {e}")
            await asyncio.sleep(0.2)

    async def run(self, shutdown: asyncio.Event) -> None:
        async with async_playwright() as pw:
            await self.start_engines(pw)
            try:
                await self._poll_loop(shutdown)
            finally:
                await self.telegram.close()

# ==========================================
# 7. ENTRY POINT
# ==========================================
async def main():
    shutdown = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: shutdown.set())
            except NotImplementedError:
                pass

    logger.info("Starting X-Watcher...")

    # Fix 4: Acc2 WS only
    asyncio.create_task(monitor(ACCOUNT2))

    orchestrator = BotOrchestrator()
    try:
        await orchestrator.run(shutdown)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        shutdown.set()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
