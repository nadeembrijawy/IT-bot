#!/usr/bin/env python3
"""
بوت بنك أسئلة الامتحانات (هندسة معلوماتية - جامعة حمص)

"""

import asyncio
import collections
import json
import re
import uuid
from io import BytesIO
import logging
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading


class HealthHandler(BaseHTTPRequestHandler):
    """يرد بـ OK فقط، بدون ما يعرض أي ملف من المجلد."""

    def _ok(self, body=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(b"OK")

    def do_GET(self):
        self._ok()

    def do_HEAD(self):  # UptimeRobot بيستخدم HEAD أحياناً
        self._ok(body=False)

    def log_message(self, *args):  # بدون سبام بالـ logs
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server running on port {port}")
    server.serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import PollType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

import stats

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "bot.db")
BATCH = 10  # عدد الأسئلة بكل دفعة
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

TRACK_NAMES = {
    "software": "برمجيات",
    "ai": "ذكاء صنعي",
    "networks": "شبكات",
}

YEAR_NAMES = {
    1: "📘 السنة الأولى",
    2: "📗 السنة الثانية",
    3: "📙 السنة الثالثة",
    4: "📕 السنة الرابعة",
    5: "📚 السنة الخامسة",
}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("qbank")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def safe(fn, *args, default=None):
    """تنفيذ دالة الإحصائيات بخيط منفصل؛ أي خطأ أو تأخير ما بيوقّف البوت."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=20)
    except Exception as e:
        log.warning("stats error in %s: %r", getattr(fn, "__name__", fn), e)
        return default


_tasks = set()


def bg(coro):
    """تشغيل بالخلفية (ما ننتظر النتيجة) حتى ما يتأخر الرد على الطالب."""
    t = asyncio.get_running_loop().create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


POLLS = collections.OrderedDict()  # poll_id -> معلومات السؤال (بالذاكرة، بدون انتظار قاعدة البيانات)
FINISHED = set()  # محاولات انرسلت كل أسئلتها
PENDING = {}  # run_id -> مهمة حفظ الـ polls بالخلفية
SEEN = set()  # مستخدمين انسجلوا بهالتشغيل
RESULT_SENT = set()  # (run_id, user_id) انبعتت نتيجتهم، حتى ما تتكرر


def remember_poll(poll_id, meta):
    POLLS[poll_id] = meta
    while len(POLLS) > 5000:
        POLLS.popitem(last=False)


def touch_once(user):
    if user and user.id not in SEEN:
        SEEN.add(user.id)
        bg(safe(stats.touch_user, user.id, user.username, user.first_name))


def subject_info(subject_id):
    with db() as con:
        return con.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# الأسئلة المسموحة: محلولة وغير مستبعدة
OK = "q.excluded = 0 AND q.correct_index IS NOT NULL"


def years():
    with db() as con:
        return [
            r[0]
            for r in con.execute(
                f"""
                SELECT DISTINCT s.year
                FROM subjects s
                JOIN questions q ON q.subject_id = s.id
                WHERE {OK}
                ORDER BY s.year
                """
            )
        ]


def semesters(year):
    with db() as con:
        return [
            r[0]
            for r in con.execute(
                f"""
                SELECT DISTINCT s.semester
                FROM subjects s
                JOIN questions q ON q.subject_id = s.id
                WHERE s.year=? AND {OK}
                ORDER BY s.semester
                """,
                (year,),
            )
        ]


def tracks(year, sem):
    """سنة 4 و5: نعرض كل الاختصاصات التي يظهر لها مواد."""
    if year < 4:
        return []
    return [
        t
        for t in ("software", "ai", "networks")
        if subjects(year, sem, t)
    ]


def subjects(year, sem, track):
    """المواد المشتركة + مواد الاختصاص المختار."""
    with db() as con:
        rows = con.execute(
            f"""
            SELECT s.id, s.name, COUNT(q.id) n
            FROM subjects s
            JOIN questions q ON q.subject_id = s.id
            WHERE s.year=?
              AND s.semester=?
              AND (s.track='shared' OR s.track=?)
              AND {OK}
            GROUP BY s.id
            ORDER BY s.name
            """,
            (year, sem, track or "shared"),
        ).fetchall()
    return rows


def reply_kb(rows, columns=2):
    """
    rows: قائمة نصوص الأزرار.
    يحولها إلى Reply Keyboard بعمودين افتراضياً،
    مثل لوحة المفاتيح الظاهرة في الصورة.
    """
    keyboard = [
        rows[i:i + columns]
        for i in range(0, len(rows), columns)
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="اختر من القائمة...",
    )


def year_menu():
    available = years()
    labels = [YEAR_NAMES[y] for y in available]
    return (
        "اختر السنة الدراسية:",
        reply_kb(labels, columns=2),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    touch_once(update.effective_user)
    text, markup = year_menu()
    await update.effective_message.reply_text(
        "أهلاً بك في بنك أسئلة الدورات السابقة 📚\n" + text,
        reply_markup=markup,
    )


def clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def set_state(context, state, **values):
    context.user_data["state"] = state
    context.user_data.update(values)


NEXT_LABEL = "➡️ التالي"


def save_in_background(run_id, rows, chat_id, subject_id, final):
    prev = PENDING.get(run_id)
    if final:
        FINISHED.add(run_id)

    async def job():
        if prev and not prev.done():
            await asyncio.wait([prev])
        if rows:
            await safe(stats.save_polls, rows)
        if final:
            await safe(stats.finish_run, run_id, chat_id, subject_id)

    PENDING[run_id] = bg(job())


async def send_batch(chat_id, context, subject_id, offset=0):
    """
    إرسال أسئلة المادة على دفعات (BATCH = 10 أسئلة).
    إذا بقي أكثر من دفعة: يظهر زر "التالي" لإرسال الدفعة التالية.
    إذا بقي BATCH سؤال أو أقل: يُرسلها كلها ثم رسالة النهاية.
    """
    with db() as con:
        subj = con.execute(
            "SELECT * FROM subjects WHERE id=?",
            (subject_id,),
        ).fetchone()

        total = con.execute(
            f"SELECT COUNT(*) FROM questions q WHERE q.subject_id=? AND {OK}",
            (subject_id,),
        ).fetchone()[0]

        rows = con.execute(
            f"""
            SELECT q.*
            FROM questions q
            WHERE q.subject_id=? AND {OK}
            ORDER BY q.id
            LIMIT ? OFFSET ?
            """,
            (subject_id, BATCH, offset),
        ).fetchall()

    if not subj:
        await context.bot.send_message(
            chat_id,
            "المادة غير موجودة.",
        )
        return

    # محاولة جديدة عند فتح المادة (offset=0)، وتكمل بنفس الرقم مع "التالي".
    if offset == 0 or not context.user_data.get("run_id"):
        context.user_data["run_id"] = uuid.uuid4().hex[:16]
    run_id = context.user_data["run_id"]
    new_rows = []

    # الترقيم يكمل من الدفعة السابقة (11، 12، ...).
    for i, q in enumerate(rows, start=offset + 1):
        if q["image"]:
            path = os.path.join(BASE, q["image"])
            if not os.path.exists(path):
                # احتياط: إذا الصور مرفوعة بجذر الريبو بدل مجلد media
                path = os.path.join(BASE, os.path.basename(q["image"]))
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        await context.bot.send_photo(chat_id, f)
                except Exception as e:
                    log.warning("photo failed q=%s: %s", q["key"], e)
            else:
                log.warning("image missing q=%s path=%s", q["key"], path)

        options = [clip(o, 100) for o in json.loads(q["options"])][:10]
        title = clip(f"{i}) {strip_num(q['question'])}", 300)

        try:
            msg = await context.bot.send_poll(
                chat_id,
                title,
                options,
                type=PollType.QUIZ,
                is_anonymous=False,
                correct_option_id=q["correct_index"],
                explanation=clip(q["explanation"] or "", 200) or None,
            )
            row = (msg.poll.id, chat_id, run_id, q["key"], subj["id"], subj["name"],
                   subj["year"], subj["semester"], q["correct_index"])
            remember_poll(msg.poll.id, row)
            new_rows.append(row)
        except Exception as e:
            # سؤال مرفوض من تيليجرام، نكمل إرسال باقي الأسئلة.
            log.warning("poll failed q=%s: %s", q["key"], e)

    sent_until = offset + len(rows)
    remaining = total - sent_until
    save_in_background(run_id, new_rows, chat_id, subject_id, final=remaining <= 0)

    if remaining > 0:
        # لسا في أسئلة: زر "التالي" (الدفعة الأخيرة قد تكون أقل من 10).
        await context.bot.send_message(
            chat_id,
            f"المعروض: {sent_until} / {total} | "
            f"المتبقي: {remaining} | اختر «التالي» للمتابعة",
            reply_markup=reply_kb(
                [NEXT_LABEL, "🏠 القائمة الرئيسية"], columns=1
            ),
        )
        set_state(
            context,
            "questions",
            subject_id=subject_id,
            offset=sent_until,
        )
        return

    # آخر دفعة انرسلت: بعد ما يجاوب الطالب على الكل بتنبعت النتيجة تلقائياً.
    # بعد إرسال آخر سؤال فقط نرسل رسالة النهاية.
    await context.bot.send_message(
        chat_id,
        "🎉 انتهت جميع الأسئلة",
        reply_markup=reply_kb(["🏠 القائمة الرئيسية"], columns=1),
    )
    set_state(context, "questions_done")


# يشيل الرقم الأصلي من بداية نص السؤال (مثل "29)" أو "5-" أو "12.")
# حتى يبقى ترقيم واحد فقط: ترقيم البوت.
_LEAD_NUM = re.compile(r"^\s*\d+\s*(?:\)|[\.\-_\u0640\u2013:]+(?!\d))\s*")


def strip_num(text):
    return _LEAD_NUM.sub("", text or "", count=1).lstrip()


async def show_years(message, context):
    context.user_data.clear()
    text, markup = year_menu()
    await message.reply_text(text, reply_markup=markup)


async def show_semesters(message, context, year):
    sems = semesters(year)
    if not sems:
        await message.reply_text(
            "لا يوجد محتوى متاح لهذه السنة.",
            reply_markup=reply_kb(["🏠 القائمة الرئيسية"], columns=1),
        )
        return

    labels = []
    for s in sems:
        name = "الأول" if s == 1 else "الثاني" if s == 2 else str(s)
        labels.append(f"📖 الفصل {name}")

    labels.append("⬅️ رجوع")
    set_state(context, "semesters", year=year)
    await message.reply_text(
        "اختر الفصل الدراسي:",
        reply_markup=reply_kb(labels, columns=2),
    )


async def show_tracks(message, context, year, sem):
    tr = tracks(year, sem)

    if not tr:
        await show_subjects(message, context, year, sem, None)
        return

    labels = [f"🎓 {TRACK_NAMES[t]}" for t in tr]
    labels.append("⬅️ رجوع")

    set_state(context, "tracks", year=year, sem=sem, tracks=tr)
    await message.reply_text(
        "اختر الاختصاص:",
        reply_markup=reply_kb(labels, columns=2),
    )


async def show_subjects(message, context, year, sem, track):
    rows = subjects(year, sem, track)

    if not rows:
        await message.reply_text(
            "لا توجد مواد متاحة.",
            reply_markup=reply_kb(["⬅️ رجوع", "🏠 القائمة الرئيسية"], columns=2),
        )
        return

    # نحفظ mapping حتى نستطيع معرفة المادة عند ضغط الزر.
    subject_map = {}
    labels = []

    for r in rows:
        label = f"📚 {r['name']} ({r['n']})"
        labels.append(label)
        subject_map[label] = r["id"]

    labels.append("⬅️ رجوع")

    set_state(
        context,
        "subjects",
        year=year,
        sem=sem,
        track=track,
        subject_map=subject_map,
    )

    await message.reply_text(
        "اختر المادة:",
        reply_markup=reply_kb(labels, columns=2),
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    text = (message.text or "").strip()
    state = context.user_data.get("state")

    if not text:
        return

    # الرئيسية تعمل من أي قائمة.
    if text in {"🏠 القائمة الرئيسية", "/start", "/menu"}:
        await show_years(message, context)
        return

    # ---------------- السنة ----------------
    if state in (None, "years"):
        year = None
        for y, label in YEAR_NAMES.items():
            if text == label:
                year = y
                break

        if year is not None:
            bg(safe(stats.log_event, message.from_user.id, "year", year))
            await show_semesters(message, context, year)
            return

        # في حال أرسل /start ولم يكن state مضبوطاً.
        if text.startswith("/"):
            await show_years(message, context)
            return

    # ---------------- الفصل ----------------
    if state == "semesters":
        year = context.user_data["year"]

        if text == "⬅️ رجوع":
            await show_years(message, context)
            return

        sem = None
        if text == "📖 الفصل الأول":
            sem = 1
        elif text == "📖 الفصل الثاني":
            sem = 2

        if sem is not None:
            await show_tracks(message, context, year, sem)
            return

    # ---------------- الاختصاص ----------------
    if state == "tracks":
        year = context.user_data["year"]
        sem = context.user_data["sem"]
        tr = context.user_data.get("tracks", [])

        if text == "⬅️ رجوع":
            await show_semesters(message, context, year)
            return

        selected_track = None
        for t in tr:
            if text == f"🎓 {TRACK_NAMES[t]}":
                selected_track = t
                break

        if selected_track:
            await show_subjects(
                message,
                context,
                year,
                sem,
                selected_track,
            )
            return

    # ---------------- المواد ----------------
    if state == "subjects":
        year = context.user_data["year"]
        sem = context.user_data["sem"]
        track = context.user_data.get("track")
        subject_map = context.user_data.get("subject_map", {})

        if text == "⬅️ رجوع":
            if track:
                await show_tracks(message, context, year, sem)
            else:
                await show_semesters(message, context, year)
            return

        subject_id = subject_map.get(text)
        if subject_id is not None:
            si = subject_info(int(subject_id))
            bg(safe(
                stats.log_event, message.from_user.id, "subject",
                si["year"], si["semester"], int(subject_id), si["name"],
            ))
            await send_batch(
                message.chat_id,
                context,
                int(subject_id),
                0,
            )
            return

    # ---------------- الدفعة التالية ----------------
    if state == "questions":
        if text == NEXT_LABEL:
            si = subject_info(int(context.user_data["subject_id"]))
            bg(safe(
                stats.log_event, message.from_user.id, "next",
                si["year"], si["semester"], si["id"], si["name"],
            ))
            await send_batch(
                message.chat_id,
                context,
                int(context.user_data["subject_id"]),
                int(context.user_data.get("offset", 0)),
            )
            return

    # ---------------- بعد انتهاء الأسئلة ----------------
    if state == "questions_done":
        if text == "🏠 القائمة الرئيسية":
            await show_years(message, context)
            return

    # ---------------- رجوع عام ----------------
    if text == "⬅️ رجوع":
        # fallback آمن إذا كانت الحالة غير معروفة
        await show_years(message, context)
        return

    await message.reply_text(
        "اختر أحد الأزرار من القائمة 👇",
        reply_markup=reply_kb(["🏠 القائمة الرئيسية"], columns=1),
    )


async def process_answer(pa, bot):
    u = pa.user
    touch_once(u)
    chosen = pa.option_ids[0]
    meta = POLLS.get(pa.poll_id)
    if meta:
        poll_id, _, run_id, key, sid, sname, year, sem, correct = meta
        task = PENDING.get(run_id)
        if task and not task.done():  # لا نكتب الإجابة قبل ما ينحفظ الـ poll
            await asyncio.wait([task], timeout=20)
        await safe(stats.insert_answer, poll_id, u.id, key, sid, sname, year, sem, chosen, correct)
        if run_id not in FINISHED:
            return  # لسا في أسئلة ما انرسلت؛ ما في داعي نسأل قاعدة البيانات
        subject_id = sid
    else:  # بعد إعادة تشغيل البوت: نرجع للقاعدة
        res = await safe(stats.record_answer, pa.poll_id, u.id, chosen)
        if not res:
            return
        run_id, subject_id, _ = res
    st = await safe(stats.run_status, u.id, run_id)
    if not st:
        return
    finished, total, right, wrong = st
    # النتيجة بس لما انرسلت كل أسئلة المادة وجاوب الطالب عليها كلها
    if finished and total and right + wrong >= total and (run_id, u.id) not in RESULT_SENT:
        RESULT_SENT.add((run_id, u.id))
        si = subject_info(subject_id)
        text = (
            f"🏁 خلصت أسئلة «{si['name']}»\n\n"
            f"✅ صح: {right}\n"
            f"❌ غلط: {wrong}\n"
            f"🎯 النسبة: {100 * right // total}%"
        )
        try:
            await bot.send_message(u.id, text)
        except Exception as e:
            log.warning("result send failed: %s", e)


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pa = update.poll_answer
    if not pa or not pa.option_ids or not pa.user:  # سحب التصويت
        return
    bg(process_answer(pa, context.bot))  # بالخلفية: ما نوقّف معالجة باقي الضغطات


def is_admin(update):
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)


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


async def post_init(app):
    bg(safe(stats.warmup))  # يجهّز الاتصال والجداول بالخلفية بدون ما يعطّل تشغيل البوت


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط التوكن بمتغير البيئة BOT_TOKEN")

    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
