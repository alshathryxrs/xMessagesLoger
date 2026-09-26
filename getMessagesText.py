import asyncio
import base64
import logging
import json
import signal
import sys
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

NOORA_USER_ID  = "2082060317358743552"
JAMILA_USER_ID = "2024978767081254912"

TELEGRAM_TOKEN   = "8630469503:AAGh-gLtUHBONPfT_1-z5oN41LCVnsWqqus"
TELEGRAM_CHAT_ID = 6607397366
PASSCODE         = "0807"
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0"

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
logging.getLogger("hpack").setLevel(logging.WARNING)

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
                    {"text": "📬 Jamila (Acc2)",  "callback_data": "check_jamila_acc2"},
                    {"text": "📸 Jamila (Acc2)",  "callback_data": "screen_jamila_acc2"},
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
            except Exception:
                pass
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

        if text:
            name = label.split(" ")[0].upper()
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
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📸 {label} {ts}"},
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

    async def answer_callback(self, cb_id: str) -> None:
        await self._post("answerCallbackQuery", json={"callback_query_id": cb_id})


# ==========================================
# 4. BROWSER ENGINE (one per account)
# ==========================================
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

# Optimized Javascript Extractor
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

    const all     = document.querySelectorAll('[data-testid^="message-"]');
    const results = [];

    for (const container of all) {
        const testId = container.getAttribute('data-testid');
        if (!/^message-[0-9a-f]{8}-/.test(testId)) continue;

        // Skip outgoing
        if (container.classList.contains('justify-end')) {
            if (!window.processedMap.has(testId)) window.processedMap.set(testId, { t: 'OUT', i: 999 });
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

        const prev = window.processedMap.get(testId) || { t: '', i: 0 };
        const imgEls = container.querySelectorAll('img[src^="blob:"], img[src*="/media/"]');
        const validImgs = Array.from(imgEls).filter(img => 
            !img.src.includes('emoji') && !img.src.includes('twemoji') && !img.src.includes('profile_images')
        );

        // Skip immediately if nothing new
        if (cleanText === prev.t && validImgs.length <= prev.i) {
            continue;
        }

        const textToSend = (cleanText && cleanText !== prev.t) ? cleanText : '';
        const imagesToSend = [];

        // Load new images only
        for (let idx = prev.i; idx < validImgs.length; idx++) {
            const b64 = await blobToBase64(validImgs[idx].src);
            if (b64) imagesToSend.push(b64);
        }

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
        logger.info(f"✅ [{self.account.label}] {name} tab ready")

    async def start(self) -> None:
        logger.info(f"🚀 [{self.account.label}] Starting browser...")
        self.ctx = await self.pw.chromium.launch_persistent_context(
            user_data_dir=self.account.user_data,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",   # critical on Railway — avoids /dev/shm crash
                "--disable-gpu",
                "--single-process",          # lower memory footprint per browser
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
        return await page.evaluate(EXTRACTOR_JS)

    async def screenshot(self, tab: str) -> bytes:
        page = self.pages.get(tab)
        if not self.ready or not page:
            raise Exception("Tab not ready")
        return await page.screenshot(full_page=False)


# ==========================================
# 5. ORCHESTRATOR
# ==========================================
class BotOrchestrator:
    def __init__(self):
        self.telegram = TelegramService()
        self.engines: Dict[str, XChatEngine] = {}
        self.ready    = False

    async def start_engines(self, pw) -> None:
        # Engine for Account 2 — watches Jamila only
        e2 = XChatEngine(ACCOUNT2, pw)
        await e2.start()
        await e2._open_tab("jamila_acc2", JAMILA_USER_ID)
        e2.ready = True
        self.engines["acc2"] = e2

        self.ready = True
        logger.info("✅ All engines ready — awaiting Telegram commands")
        await self.telegram.send_text(
            "🤖 *X-Watcher Ready*\n\nGhost Mode active on all tabs.",
        )

    async def handle_command(self, command: str) -> None:
        if not self.ready:
            await self.telegram.send_text("⏳ *Still booting. Please wait.*", with_menu=False)
            return

        # /check_noora_acc1  /check_jamila_acc1  /check_jamila_acc2
        if command.startswith("/check_"):
            parts  = command[7:].split("_")
            name   = parts[0]
            acct   = parts[1] if len(parts) > 1 else "acc2"
            engine = self.engines.get(acct)
            tab    = f"{name}_{acct}"
            label  = f"{name.capitalize()} ({acct.upper()})"
            logger.info(f"Fetching {label}...")

            if not engine:
                await self.telegram.send_text(f"❌ No engine for {acct}")
                return

            msgs = await engine.get_messages(tab)
            if not msgs:
                await self.telegram.send_text(f"📭 *No new messages from {label}.*")
            else:
                for m in msgs:
                    await self.telegram.send_payload(m, label)
                await self.telegram.send_text(f"✅ *Done — {label}.*")

        elif command.startswith("/screen_"):
            parts  = command[8:].split("_")
            name   = parts[0]
            acct   = parts[1] if len(parts) > 1 else "acc2"
            engine = self.engines.get(acct)
            tab    = f"{name}_{acct}"
            label  = f"{name.capitalize()} ({acct.upper()})"
            logger.info(f"Screenshot {label}...")
            try:
                img = await engine.screenshot(tab)
                await self.telegram.send_screenshot(img, f"📸 {label}")
            except Exception as e:
                await self.telegram.send_text(f"❌ Screenshot failed: {e}")

        elif command.startswith("/open "):
            # /open <user_id> [acc1|acc2]
            # Example: /open 2082060317358743552 acc2
            parts   = command[6:].strip().split()
            user_id = parts[0] if parts else ""
            acct    = parts[1] if len(parts) > 1 else "acc2"
            engine  = self.engines.get(acct)

            if not user_id.isdigit():
                await self.telegram.send_text("❌ Invalid user ID. Usage: `/open <user_id> [acc1|acc2]`")
                return
            if not engine:
                await self.telegram.send_text(f"❌ No engine for {acct}")
                return

            tab_name = f"custom_{user_id}_{acct}"
            if tab_name in engine.pages:
                await self.telegram.send_text(f"✅ Tab already open for `{user_id}` on {acct.upper()}")
                return

            await self.telegram.send_text(f"⏳ Opening tab for `{user_id}` on {acct.upper()}...")
            try:
                await engine._open_tab(tab_name, user_id)
                await self.telegram.send_text(
                    f"✅ Tab ready for `{user_id}` on {acct.upper()}\n"
                    f"Use `/check_{tab_name}` or `/screen_{tab_name}` to read it.",
                    with_menu=False,
                )
            except Exception as e:
                await self.telegram.send_text(f"❌ Failed to open tab: {e}")

        elif command in ("/status", "/start"):
            tabs = {acct: list(e.pages.keys()) for acct, e in self.engines.items()}
            await self.telegram.send_text(
                f"🤖 *X-Watcher Online*\n\nGhost Mode active.\nOpen tabs: `{tabs}`",
            )

    async def run(self, shutdown: asyncio.Event) -> None:
        async with async_playwright() as pw:
            await self.start_engines(pw)
            try:
                while not shutdown.is_set():
                    updates = await self.telegram.poll()
                    for upd in updates:
                        command    = ""
                        chat_id    = None
                        cb_id      = None

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
                        await self.handle_command(command)

                    await asyncio.sleep(1)
            finally:
                await self.telegram.close()


# ==========================================
# 6. ENTRY POINT
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
    orchestrator = BotOrchestrator()
    try:
        task = asyncio.create_task(orchestrator.run(shutdown))
        while not task.done():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        shutdown.set()
        await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
