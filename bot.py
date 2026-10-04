#!/usr/bin/env python3
"""
بوت بنك أسئلة الامتحانات (هندسة معلوماتية - جامعة حمص)
السنة -> الفصل -> (الاختصاص بسنة 4 و5) -> المادة -> أسئلة quiz بدفعات.

التشغيل المحلي (polling):   BOT_TOKEN=xxxx python bot.py
التشغيل على Render (webhook): يتفعّل تلقائياً لما يوجد RENDER_EXTERNAL_URL
  المتغيرات: BOT_TOKEN  +  WEBHOOK_SECRET (أي نص من حروف وأرقام و _ و -)
"""
import asyncio
import json
import logging
import os
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import PollType
from telegram.error import RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "bot.db")
BATCH = 10  # عدد الأسئلة بكل دفعة

TRACK_NAMES = {"software": "برمجيات", "ai": "ذكاء صنعي", "networks": "شبكات"}
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # عشان ما يظهر التوكن بالـ logs
log = logging.getLogger("qbank")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# الأسئلة المسموحة: محلولة وغير مستبعدة
OK = "q.excluded = 0 AND q.correct_index IS NOT NULL"


def years():
    with db() as con:
        return [r[0] for r in con.execute(
            f"SELECT DISTINCT s.year FROM subjects s JOIN questions q ON q.subject_id=s.id WHERE {OK} ORDER BY s.year")]


def semesters(year):
    with db() as con:
        return [r[0] for r in con.execute(
            f"SELECT DISTINCT s.semester FROM subjects s JOIN questions q ON q.subject_id=s.id "
            f"WHERE s.year=? AND {OK} ORDER BY s.semester", (year,))]


def subjects(year, sem, track):
    """المواد المشتركة + مواد الاختصاص المختار"""
    with db() as con:
        rows = con.execute(
            f"SELECT s.id, s.name, COUNT(q.id) n FROM subjects s JOIN questions q ON q.subject_id=s.id "
            f"WHERE s.year=? AND s.semester=? AND (s.track='shared' OR s.track=?) AND {OK} "
            f"GROUP BY s.id ORDER BY s.name", (year, sem, track or "shared")).fetchall()
    return rows


def tracks(year, sem):
    """سنة 4 و5: نعرض كل الاختصاصات التي يظهر لها مواد (المشتركة + الخاصة بها)"""
    if year < 4:
        return []
    return [t for t in ("software", "ai", "networks") if subjects(year, sem, t)]


def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t, d in rows])


def year_menu():
    names = {1: "السنة الأولى", 2: "السنة الثانية", 3: "السنة الثالثة", 4: "السنة الرابعة", 5: "السنة الخامسة"}
    return "اختر السنة الدراسية:", kb([(names[y], f"y:{y}") for y in years()])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = year_menu()
    await update.effective_message.reply_text("أهلاً بك في بنك أسئلة الدورات السابقة 📚\n" + text, reply_markup=markup)


def clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def find_image(rel):
    """يدوّر على الصورة بمجلد media أو بجانب bot.py (إذا انرفعت الصور بدون مجلد)"""
    for p in (os.path.join(BASE, rel), os.path.join(BASE, os.path.basename(rel)),
              os.path.join(BASE, "media", os.path.basename(rel))):
        if os.path.exists(p):
            return p
    return None


async def with_retry(fn, *args, **kwargs):
    """إعادة المحاولة إذا طلب تيليجرام الانتظار (حد سرعة الإرسال)"""
    for _ in range(3):
        try:
            return await fn(*args, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 1)
    return await fn(*args, **kwargs)


async def send_batch(chat_id, context, subject_id, offset):
    with db() as con:
        subj = con.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()
        rows = con.execute(
            f"SELECT q.* FROM questions q WHERE q.subject_id=? AND {OK} ORDER BY q.id LIMIT ? OFFSET ?",
            (subject_id, BATCH, offset)).fetchall()
        total = con.execute(f"SELECT COUNT(*) FROM questions q WHERE q.subject_id=? AND {OK}", (subject_id,)).fetchone()[0]

    for i, q in enumerate(rows, start=offset + 1):
        if q["image"]:
            path = find_image(q["image"])
            if path:
                with open(path, "rb") as f:
                    await with_retry(context.bot.send_photo, chat_id, f)
            else:
                log.warning("image missing for %s: %s", q["key"], q["image"])
        options = [clip(o, 100) for o in json.loads(q["options"])][:10]
        title = clip(f"{i}) {q['question']}", 300)
        try:
            await with_retry(
                context.bot.send_poll,
                chat_id, title, options, type=PollType.QUIZ, is_anonymous=False,
                correct_option_id=q["correct_index"],
                explanation=clip(q["explanation"] or "", 200) or None)
        except Exception as e:  # سؤال مرفوض من تيليجرام
            log.warning("poll failed q=%s: %s", q["key"], e)
        await asyncio.sleep(0.3)

    done = offset + len(rows)
    buttons = []
    if done < total:
        buttons.append(("التالي ⏭ (%d/%d)" % (done, total), f"n:{subject_id}:{done}"))
    buttons.append(("🏠 القائمة الرئيسية", "home"))
    msg = f"انتهت دفعة أسئلة «{subj['name']}»" if done < total else f"🎉 خلصت كل أسئلة «{subj['name']}»"
    await with_retry(context.bot.send_message, chat_id, msg, reply_markup=kb(buttons))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    chat_id = q.message.chat_id

    if d == "home":
        text, markup = year_menu()
        await context.bot.send_message(chat_id, text, reply_markup=markup)
    elif d.startswith("y:"):
        y = int(d[2:])
        rows = [(f"الفصل {'الأول' if s == 1 else 'الثاني'}", f"s:{y}:{s}") for s in semesters(y)]
        rows.append(("⬅ رجوع", "home"))
        await q.edit_message_text("اختر الفصل الدراسي:", reply_markup=kb(rows))
    elif d.startswith("s:"):
        _, y, s = d.split(":")
        y, s = int(y), int(s)
        tr = tracks(y, s)
        if tr:
            rows = [(TRACK_NAMES[t], f"t:{y}:{s}:{t}") for t in tr]
            rows.append(("⬅ رجوع", f"y:{y}"))
            await q.edit_message_text("اختر الاختصاص:", reply_markup=kb(rows))
        else:
            await show_subjects(q, y, s, None)
    elif d.startswith("t:"):
        _, y, s, t = d.split(":")
        await show_subjects(q, int(y), int(s), t)
    elif d.startswith("sub:"):
        await send_batch(chat_id, context, int(d[4:]), 0)
    elif d.startswith("n:"):
        _, sid, off = d.split(":")
        await send_batch(chat_id, context, int(sid), int(off))


async def show_subjects(q, y, s, track):
    rows = [(f"{r['name']} ({r['n']})", f"sub:{r['id']}") for r in subjects(y, s, track)]
    back = f"s:{y}:{s}" if track else f"y:{y}"
    rows.append(("⬅ رجوع", back))
    await q.edit_message_text("اختر المادة:", reply_markup=kb(rows))


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("handler error: %s", context.error)


def build_app(token, webhook=False):
    builder = Application.builder().token(token).concurrent_updates(True)
    if webhook:
        builder = builder.updater(None)  # الاستقبال عبر خادم الويب تبعنا
    app = builder.build()
    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    return app


def make_web_app(application, secret):
    """خادم صغير: /telegram لاستقبال تحديثات تيليجرام، و / لفحص الحياة (UptimeRobot)"""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse, Response
    from starlette.routing import Route

    async def telegram(request):
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return Response(status_code=403)
        data = await request.json()
        await application.update_queue.put(Update.de_json(data, application.bot))
        return Response()

    async def health(request):
        return PlainTextResponse("OK")

    return Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/", health, methods=["GET", "HEAD"]),
    ])


async def run_webhook(token, base_url, secret, port):
    import uvicorn

    application = build_app(token, webhook=True)
    web = uvicorn.Server(uvicorn.Config(make_web_app(application, secret), host="0.0.0.0", port=port, log_level="warning"))
    async with application:
        await application.start()
        await application.bot.set_webhook(
            url=f"{base_url.rstrip('/')}/telegram", secret_token=secret,
            allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
        log.info("webhook mode started on port %s", port)
        await web.serve()
        await application.stop()


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط التوكن بمتغير البيئة BOT_TOKEN")
    base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base_url:
        secret = os.environ.get("WEBHOOK_SECRET")
        if not secret:
            raise SystemExit("حط WEBHOOK_SECRET بمتغيرات البيئة (حروف وأرقام و _ و -)")
        asyncio.run(run_webhook(token, base_url, secret, int(os.environ.get("PORT", "10000"))))
    else:
        log.info("polling mode")
        build_app(token).run_polling()


if __name__ == "__main__":
    main()
