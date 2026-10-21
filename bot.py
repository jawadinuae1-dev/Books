"""
NEURAL MIND BOT
Daily viral-book insight (morning) + psychology topic (evening),
bilingual English/Urdu, real-life apply steps, quiz polls, score tracking.
"""
import asyncio
import datetime as dt
import json
import logging
import os
import random
import sqlite3
from html import escape
from zoneinfo import ZoneInfo

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
)

from content import BOOKS, PSYCH

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("neural-mind")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ───────────────────────── config ─────────────────────────
def _env(name: str, default: str = "") -> str:
    """Env var with stray spaces/quotes removed (common copy-paste mistake on Railway)."""
    return os.getenv(name, default).strip().strip('"').strip("'").strip()


BOT_TOKEN = _env("BOT_TOKEN")
GEMINI_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
# Tried in order; the first one that works is remembered.
GEMINI_MODELS = list(
    dict.fromkeys(
        m
        for m in (GEMINI_MODEL, "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash")
        if m
    )
)
ACTIVE_MODEL = GEMINI_MODELS[0]
LAST_ERROR = "abhi tak koi call nahi hui"
TZ = ZoneInfo(os.getenv("BOT_TZ", "Asia/Dubai"))
DATA_DIR = os.getenv("DATA_DIR", "data")


def _t(value: str) -> dt.time:
    h, m = value.split(":")
    return dt.time(int(h), int(m), tzinfo=TZ)


BOOK_TIME_STR = os.getenv("BOOK_TIME", "08:00")
PSYCH_TIME_STR = os.getenv("PSYCH_TIME", "20:00")
BAR = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰"

# ───────────────────────── database ─────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA_DIR, "bot.db"), check_same_thread=False)
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(chat_id INTEGER PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS polls(poll_id TEXT PRIMARY KEY, correct INTEGER);
CREATE TABLE IF NOT EXISTS scores(user_id INTEGER PRIMARY KEY, name TEXT,
                                  correct_n INTEGER DEFAULT 0, total_n INTEGER DEFAULT 0);
"""
)
db.commit()

# ───────────────────────── AI (Gemini free API) ─────────────────────────
QUIZ_SCHEMA = """{
  "q_en": "question in simple English, max 130 chars",
  "q_ur": "same question in simple Urdu script, max 130 chars",
  "options": ["4 options, each formatted 'English / اردو', max 80 chars each"],
  "correct": 0,
  "why": "one-line explanation in simple English, max 150 chars"
}"""


def lesson_prompt(kind: str, item: str) -> str:
    if kind == "book":
        subject = f'the book "{item}"'
        angle = (
            "ONE powerful, specific idea from this book. Explain it in your own words "
            "(no direct quotes, no long excerpts)"
        )
    else:
        subject = f'the psychology topic "{item}"'
        angle = "the core idea, one well-known study or finding if there is one, and why it matters in daily life"
    return f"""You are a sharp, warm mentor writing a daily lesson about {subject}.
Cover: {angle}.
Return ONLY valid JSON with exactly these keys:
{{
  "title_en": "catchy title, max 8 words",
  "summary_en": "60-90 words, simple English",
  "apply_en": ["3 concrete real-life steps a normal person can do, each under 25 words"],
  "challenge_en": "one 5-minute action for today, under 25 words",
  "title_ur": "same title in Urdu script",
  "summary_ur": "same idea in natural everyday Urdu (Urdu script), not a stiff translation",
  "apply_ur": ["same 3 steps in Urdu"],
  "challenge_ur": "same challenge in Urdu",
  "quiz": {QUIZ_SCHEMA}
}}
The quiz must test understanding or application of today's lesson. Randomize which option is correct (0-3)."""


def parse_json(text: str):
    """Parse model output; tolerate ```json fences and text around the JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _api_error(resp: httpx.Response) -> tuple[str, str]:
    """(status_name, message) from a Gemini error response."""
    try:
        err = resp.json()["error"]
        return str(err.get("status", "")), str(err.get("message", ""))
    except Exception:  # noqa: BLE001
        return "", resp.text[:150]


async def gemini_json(prompt: str):
    """Call Gemini, return parsed JSON or None. Sets LAST_ERROR with the real reason on failure."""
    global ACTIVE_MODEL, LAST_ERROR
    models = [ACTIVE_MODEL] + [m for m in GEMINI_MODELS if m != ACTIVE_MODEL]
    async with httpx.AsyncClient(timeout=90) as client:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            gen_cfg = {"temperature": 0.9, "responseMimeType": "application/json", "maxOutputTokens": 4096}
            if "2.5-flash" in model:
                gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # no hidden thinking: faster, no truncation
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
            for attempt in range(2):
                try:
                    r = await client.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body)
                    if r.status_code != 200:
                        status, msg = _api_error(r)
                        LAST_ERROR = f"{model}: HTTP {r.status_code} {status} - {msg}"[:200]
                        log.warning("Gemini error: %s", LAST_ERROR)
                        key_problem = r.status_code in (401, 403) or "API key" in msg or "API_KEY" in msg
                        if key_problem or status == "FAILED_PRECONDITION":
                            return None  # same for every model: key/region/billing problem
                        if r.status_code in (429, 500, 503) and attempt == 0:
                            await asyncio.sleep(4)
                            continue
                        break  # 404 / 400 / still limited -> try next model
                    payload = r.json()
                    cands = payload.get("candidates") or []
                    if not cands or "content" not in cands[0] or not cands[0]["content"].get("parts"):
                        reason = (cands[0].get("finishReason") if cands else None) or payload.get("promptFeedback")
                        LAST_ERROR = f"{model}: khaali jawab ({reason})"[:200]
                        log.warning("Gemini error: %s", LAST_ERROR)
                        continue
                    data = parse_json(cands[0]["content"]["parts"][0]["text"])
                    if isinstance(data, list) and data:
                        data = data[0]
                    ACTIVE_MODEL = model
                    return data
                except Exception as exc:  # noqa: BLE001 - timeouts, bad JSON, network
                    LAST_ERROR = f"{model}: {type(exc).__name__} {exc}"[:200]
                    log.warning("Gemini error: %s", LAST_ERROR)
                    await asyncio.sleep(2)
    return None


def valid_quiz(q) -> bool:
    try:
        assert isinstance(q["options"], list) and len(q["options"]) == 4
        q["correct"] = int(q["correct"])
        assert q["correct"] in range(4)
        for k in ("q_en", "q_ur", "why"):
            assert isinstance(q[k], str) and q[k].strip()
        return True
    except Exception:  # noqa: BLE001
        return False


def valid_lesson(d) -> bool:
    try:
        for k in ("title_en", "summary_en", "challenge_en", "title_ur", "summary_ur", "challenge_ur"):
            assert isinstance(d[k], str) and d[k].strip()
        for k in ("apply_en", "apply_ur"):
            assert isinstance(d[k], list) and len(d[k]) >= 2
        return valid_quiz(d["quiz"])
    except Exception:  # noqa: BLE001
        return False


async def generate_lesson(kind: str, item: str):
    global LAST_ERROR
    for _ in range(3):
        d = await gemini_json(lesson_prompt(kind, item))
        if d is None:
            return None  # gemini_json already recorded the reason
        if valid_lesson(d):
            return d
        LAST_ERROR = "AI ka jawab adhura/galat format mein tha"
    return None


def fail_text() -> str:
    return f"⚠️ Signal lost. Thori der baad dobara try karein.\n🔧 Wajah: {LAST_ERROR}"


async def generate_quiz():
    item = random.choice(BOOKS + PSYCH)
    prompt = (
        f'Create ONE fair but thought-provoking multiple-choice question about: "{item}". '
        f"Return ONLY valid JSON in exactly this shape:\n{QUIZ_SCHEMA}\n"
        "Randomize which option is correct (0-3)."
    )
    global LAST_ERROR
    for _ in range(3):
        q = await gemini_json(prompt)
        if q is None:
            return None
        if valid_quiz(q):
            return q
        LAST_ERROR = "AI ka quiz jawab galat format mein tha"
    return None


# ───────────────────────── formatting ─────────────────────────
def esc(text: str) -> str:
    return escape(str(text).replace("**", "").replace("*", "").strip())


def trunc(text: str, limit: int) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_en(kind: str, item: str, d: dict) -> str:
    head = "📘 <b>NEURAL LIBRARY · DAILY DROP</b>" if kind == "book" else "🧬 <b>MIND LAB · DAILY DROP</b>"
    steps = "\n".join(f"<b>{i}.</b> {esc(s)}" for i, s in enumerate(d["apply_en"][:3], 1))
    return (
        f"{head}\n<code>{BAR}</code>\n"
        f"◈ <b>{esc(item)}</b>\n\n"
        f"💡 <b>{esc(d['title_en'])}</b>\n{esc(d['summary_en'])}\n\n"
        f"⚙️ <b>APPLY IN REAL LIFE</b>\n{steps}\n\n"
        f"🎯 <b>TODAY'S MISSION</b>\n<blockquote>{esc(d['challenge_en'])}</blockquote>"
    )


def fmt_ur(kind: str, item: str, d: dict) -> str:
    head = "📘 <b>نیورل لائبریری · روزانہ ڈوز</b>" if kind == "book" else "🧬 <b>مائنڈ لیب · روزانہ ڈوز</b>"
    steps = "\n".join(f"<b>{i}.</b> {esc(s)}" for i, s in enumerate(d["apply_ur"][:3], 1))
    return (
        f"{head}\n<code>{BAR}</code>\n"
        f"◈ <b>{esc(item)}</b>\n\n"
        f"💡 <b>{esc(d['title_ur'])}</b>\n{esc(d['summary_ur'])}\n\n"
        f"⚙️ <b>حقیقی زندگی میں کیسے اپلائی کریں</b>\n{steps}\n\n"
        f"🎯 <b>آج کا مشن</b>\n<blockquote>{esc(d['challenge_ur'])}</blockquote>"
    )


def menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardButton
    return InlineKeyboardMarkup(
        [
            [b("📚 New Book", callback_data="book"), b("🧬 Psychology", callback_data="psych")],
            [b("🎯 Quiz Me", callback_data="quiz"), b("📊 My Stats", callback_data="score")],
        ]
    )


def rank(correct: int) -> str:
    if correct >= 50:
        return "🔮 Mastermind"
    if correct >= 20:
        return "🛰 Architect"
    if correct >= 5:
        return "⚡ Operator"
    return "🔹 Initiate"


# ───────────────────────── delivery ─────────────────────────
async def send_quiz(bot, chat_id: int, q: dict):
    question = trunc(f"{q['q_en']}\n{q['q_ur']}", 300)
    options = [trunc(o, 100) for o in q["options"]]
    msg = await bot.send_poll(
        chat_id=chat_id,
        question=question,
        options=options,
        type="quiz",
        correct_option_id=q["correct"],
        is_anonymous=False,
        explanation=trunc(q["why"], 200),
    )
    db.execute("INSERT OR REPLACE INTO polls(poll_id, correct) VALUES(?, ?)", (msg.poll.id, q["correct"]))
    db.commit()


async def deliver(bot, chat_id: int, kind: str, item: str, d: dict):
    await bot.send_message(chat_id, fmt_en(kind, item, d), parse_mode=ParseMode.HTML)
    await bot.send_message(chat_id, fmt_ur(kind, item, d), parse_mode=ParseMode.HTML, reply_markup=menu())
    await send_quiz(bot, chat_id, d["quiz"])


def pick_daily(kind: str) -> str:
    pool, offset = (BOOKS, 0) if kind == "book" else (PSYCH, 7)
    return pool[(dt.datetime.now(TZ).date().toordinal() + offset) % len(pool)]


async def on_demand(bot, chat_id: int, kind: str):
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    today = pick_daily(kind)
    pool = BOOKS if kind == "book" else PSYCH
    item = random.choice([x for x in pool if x != today])
    d = await generate_lesson(kind, item)
    if not d:
        await bot.send_message(chat_id, fail_text())
        return
    await deliver(bot, chat_id, kind, item, d)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    kind = context.job.data
    item = pick_daily(kind)
    d = await generate_lesson(kind, item)
    if not d:
        log.error("Daily %s generation failed: %s", kind, LAST_ERROR)
        return
    chats = [r[0] for r in db.execute("SELECT chat_id FROM users WHERE active=1").fetchall()]
    log.info("Broadcasting %s (%s) to %s users", kind, item, len(chats))
    for chat_id in chats:
        try:
            await deliver(context.bot, chat_id, kind, item, d)
        except Forbidden:
            db.execute("UPDATE users SET active=0 WHERE chat_id=?", (chat_id,))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Send to %s failed: %s", chat_id, exc)
        await asyncio.sleep(0.1)


# ───────────────────────── handlers ─────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.execute(
        "INSERT INTO users(chat_id, name, active) VALUES(?, ?, 1) "
        "ON CONFLICT(chat_id) DO UPDATE SET active=1, name=excluded.name",
        (update.effective_chat.id, u.first_name),
    )
    db.commit()
    text = (
        f"🤖 <b>NEURAL MIND // ONLINE</b>\n<code>{BAR}</code>\n"
        f"Salaam <b>{esc(u.first_name)}</b>! Main aapka daily mind-upgrade bot hoon.\n\n"
        f"📘 Roz <b>{BOOK_TIME_STR}</b> — viral book ka naya idea\n"
        f"🧬 Roz <b>{PSYCH_TIME_STR}</b> — naya psychology topic\n"
        f"🎯 Har drop ke saath quiz\n"
        f"🌐 English + اردو, real-life apply steps ke saath\n\n"
        f"Abhi try karein 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu())


async def cmd_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await on_demand(context.bot, update.effective_chat.id, "book")


async def cmd_psych(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await on_demand(context.bot, update.effective_chat.id, "psych")


async def do_quiz(bot, chat_id: int):
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    q = await generate_quiz()
    if not q:
        await bot.send_message(chat_id, fail_text())
        return
    await send_quiz(bot, chat_id, q)


async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_quiz(context.bot, update.effective_chat.id)


async def do_score(bot, chat_id: int, user_id: int):
    row = db.execute("SELECT correct_n, total_n FROM scores WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row[1]:
        await bot.send_message(chat_id, "📊 Abhi koi quiz solve nahi hua. /quiz se shuru karein!")
        return
    c, t = row
    await bot.send_message(
        chat_id,
        f"📊 <b>YOUR NEURAL STATS</b>\n<code>{BAR}</code>\n"
        f"Rank: <b>{rank(c)}</b>\n✅ Sahi jawab: <b>{c}</b> / {t}\n🎯 Accuracy: <b>{round(100 * c / t)}%</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_score(context.bot, update.effective_chat.id, update.effective_user.id)


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Self-test: tells you exactly why Gemini works or fails."""
    await update.message.reply_text("🔧 Gemini test chal raha hai…")
    d = await gemini_json('Return ONLY this JSON: {"ok": true}')
    key_info = f"Key length: {len(GEMINI_KEY)} (aam tor par 39), 'AIza' se shuru: {GEMINI_KEY.startswith('AIza')}"
    if d:
        text = f"✅ Gemini theek hai.\nModel: {ACTIVE_MODEL}\n{key_info}"
    else:
        text = f"❌ Gemini fail.\nWajah: {LAST_ERROR}\n{key_info}"
    await update.message.reply_text(text)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.execute("UPDATE users SET active=0 WHERE chat_id=?", (update.effective_chat.id,))
    db.commit()
    await update.message.reply_text("🔕 Daily drops band. Wapis shuru karne ke liye /start likhein.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id
    if q.data in ("book", "psych"):
        await on_demand(context.bot, chat_id, q.data)
    elif q.data == "quiz":
        await do_quiz(context.bot, chat_id)
    elif q.data == "score":
        await do_score(context.bot, chat_id, q.from_user.id)


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = update.poll_answer
    if not a.user or not a.option_ids:
        return
    row = db.execute("SELECT correct FROM polls WHERE poll_id=?", (a.poll_id,)).fetchone()
    if not row:
        return
    ok = 1 if a.option_ids[0] == row[0] else 0
    db.execute(
        "INSERT INTO scores(user_id, name, correct_n, total_n) VALUES(?, ?, ?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET correct_n=correct_n+?, total_n=total_n+1, name=excluded.name",
        (a.user.id, a.user.first_name, ok, ok),
    )
    db.commit()


async def post_init(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Bot shuru karein"),
            BotCommand("book", "Naya viral book idea"),
            BotCommand("psych", "Naya psychology topic"),
            BotCommand("quiz", "Quiz khelein"),
            BotCommand("score", "Apni stats dekhein"),
            BotCommand("diag", "Bot ka self-test"),
            BotCommand("stop", "Daily drops band karein"),
        ]
    )


def main():
    if not BOT_TOKEN or not GEMINI_KEY:
        raise SystemExit("BOT_TOKEN aur GEMINI_API_KEY environment variables set karein.")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("book", cmd_book))
    app.add_handler(CommandHandler("psych", cmd_psych))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    app.job_queue.run_daily(daily_job, _t(BOOK_TIME_STR), data="book", name="daily-book")
    app.job_queue.run_daily(daily_job, _t(PSYCH_TIME_STR), data="psych", name="daily-psych")
    log.info("Neural Mind online. Book %s | Psychology %s | TZ %s", BOOK_TIME_STR, PSYCH_TIME_STR, TZ)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
