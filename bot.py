#!/usr/bin/env python3
"""
بوت بنك أسئلة الامتحانات (هندسة معلوماتية - جامعة حمص)
السنة -> الفصل -> (الاختصاص بسنة 4 و5) -> المادة -> أسئلة quiz بدفعات.


"""
import asyncio
import json
import logging
import os
import sqlite3
import uuid
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import PollType
from telegram.error import RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, PollAnswerHandler

import stats

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "bot.db")
BATCH = 10  # عدد الأسئلة بكل دفعة
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}

TRACK_NAMES = {"software": "برمجيات", "ai": "ذكاء صنعي", "networks": "شبكات"}
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # عشان ما يظهر التوكن بالـ logs
log = logging.getLogger("qbank")


async def safe(fn, *args, default=None):
    """تنفيذ دالة الإحصائيات بخيط منفصل؛ أي خطأ ما بيوقّف البوت."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as e:
        log.warning("stats error in %s: %s", getattr(fn, "__name__", fn), e)
        return default


def subject_info(subject_id):
    with db() as con:
        return con.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()


def subject_total(subject_id):
    with db() as con:
        return con.execute(f"SELECT COUNT(*) FROM questions q WHERE q.subject_id=? AND {OK}", (subject_id,)).fetchone()[0]


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
    u = update.effective_user
    if u:
        await safe(stats.touch_user, u.id, u.username, u.first_name)
    text, markup = year_menu()
    await update.effective_message.reply_text("أهلاً بك في بنك أسئلة الدورات السابقة 📚\n" + text, reply_markup=markup)


def clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


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

    batch_id = uuid.uuid4().hex[:12]
    sent = 0
    for i, q in enumerate(rows, start=offset + 1):
        if q["image"]:
            path = os.path.join(BASE, q["image"])
            if os.path.exists(path):
                with open(path, "rb") as f:
                    await with_retry(context.bot.send_photo, chat_id, f)
        options = [clip(o, 100) for o in json.loads(q["options"])][:10]
        title = clip(f"{i}) {q['question']}", 300)
        try:
            msg = await with_retry(
                context.bot.send_poll,
                chat_id, title, options, type=PollType.QUIZ, is_anonymous=False,
                correct_option_id=q["correct_index"],
                explanation=clip(q["explanation"] or "", 200) or None)
            sent += 1
            await safe(stats.save_poll, msg.poll.id, chat_id, batch_id, q["key"], subj["id"], subj["name"],
                       subj["year"], subj["semester"], q["correct_index"])
        except Exception as e:  # سؤال مرفوض من تيليجرام
            log.warning("poll failed q=%s: %s", q["key"], e)
        await asyncio.sleep(0.3)

    done = offset + len(rows)
    buttons = []
    if sent:
        buttons.append(("📊 نتيجتي بهي الدفعة", f"r:{batch_id}:{subject_id}"))
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
        await safe(stats.log_event, q.from_user.id, "year", y)
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
        sid = int(d[4:])
        si = subject_info(sid)
        await safe(stats.log_event, q.from_user.id, "subject", si["year"], si["semester"], sid, si["name"])
        await send_batch(chat_id, context, sid, 0)
    elif d.startswith("n:"):
        _, sid, off = d.split(":")
        si = subject_info(int(sid))
        await safe(stats.log_event, q.from_user.id, "next", si["year"], si["semester"], int(sid), si["name"])
        await send_batch(chat_id, context, int(sid), int(off))
    elif d.startswith("r:"):
        _, batch_id, sid = d.split(":")
        text = await result_text(q.from_user.id, batch_id, int(sid))
        await context.bot.send_message(chat_id, text)


async def show_subjects(q, y, s, track):
    rows = [(f"{r['name']} ({r['n']})", f"sub:{r['id']}") for r in subjects(y, s, track)]
    back = f"s:{y}:{s}" if track else f"y:{y}"
    rows.append(("⬅ رجوع", back))
    await q.edit_message_text("اختر المادة:", reply_markup=kb(rows))


async def result_text(user_id, batch_id, subject_id):
    si = subject_info(subject_id)
    br = await safe(stats.batch_result, user_id, batch_id)
    sr = await safe(stats.subject_result, user_id, subject_id)
    if not br:
        return "ما قدرت أجيب النتيجة هلأ، جرّب بعد شوي 🙏"
    total, right, wrong = br
    skipped = total - right - wrong
    lines = [f"📊 نتيجتك بدفعة «{si['name']}»", "",
             f"✅ صح: {right}", f"❌ غلط: {wrong}"]
    if skipped > 0:
        lines.append(f"⏳ ما جاوبت: {skipped}")
    if total:
        lines.append(f"🎯 النسبة: {100 * right // total}%")
    if sr:
        sright, swrong = sr
        lines += ["", f"📚 مجموعك بالمادة لهلأ: ✅ {sright}  ❌ {swrong}  (من {subject_total(subject_id)} سؤال)"]
    return "\n".join(lines)


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pa = update.poll_answer
    if not pa or not pa.option_ids or not pa.user:  # سحب التصويت
        return
    u = pa.user
    await safe(stats.touch_user, u.id, u.username, u.first_name)
    res = await safe(stats.record_answer, pa.poll_id, u.id, pa.option_ids[0])
    if res is None:  # ممكن الإجابة وصلت قبل حفظ الـ poll
        await asyncio.sleep(1.5)
        res = await safe(stats.record_answer, pa.poll_id, u.id, pa.option_ids[0])
    if not res:
        return
    batch_id, subject_id, _ = res
    br = await safe(stats.batch_result, u.id, batch_id)
    # لما يخلص الطالب كل أسئلة الدفعة، نرسل له النتيجة تلقائياً
    if br and br[0] and br[1] + br[2] >= br[0]:
        await with_retry(context.bot.send_message, u.id, await result_text(u.id, batch_id, subject_id))


def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    text = await safe(stats.overview, default="تعذّر قراءة الإحصائيات (تحقق من DATABASE_URL).")
    await update.effective_message.reply_text(text)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    data = await safe(stats.export_csv)
    if not data:
        await update.effective_message.reply_text("تعذّر التصدير.")
        return
    await update.effective_message.reply_document(BytesIO(data), filename="answers.csv")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"رقمك: {update.effective_user.id}")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("handler error: %s", context.error)


def build_app(token, webhook=False):
    builder = Application.builder().token(token).concurrent_updates(True)
    if webhook:
        builder = builder.updater(None)  # الاستقبال عبر خادم الويب تبعنا
    app = builder.build()
    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(PollAnswerHandler(on_poll_answer))
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
    stats.init()
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
        build_app(token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
