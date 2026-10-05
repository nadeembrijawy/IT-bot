#!/usr/bin/env python3
"""
بوت بنك أسئلة الامتحانات (هندسة معلوماتية - جامعة حمص)

التنقل:
السنة -> الفصل -> (الاختصاص بسنة 4 و5) -> المادة -> أسئلة Quiz بدفعات.

تم تحويل قوائم التنقل من Inline Keyboard إلى Reply Keyboard
حتى تظهر كأزرار كبيرة أسفل المحادثة مثل لوحة المفاتيح في الصورة.

التشغيل:
    BOT_TOKEN=xxxx python bot_reply_keyboard.py
"""

import json
import logging
import os
import sqlite3

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import PollType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "bot.db")
BATCH = 10  # عدد الأسئلة بكل دفعة

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


async def send_batch(chat_id, context, subject_id, offset):
    with db() as con:
        subj = con.execute(
            "SELECT * FROM subjects WHERE id=?",
            (subject_id,),
        ).fetchone()

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

        total = con.execute(
            f"""
            SELECT COUNT(*)
            FROM questions q
            WHERE q.subject_id=? AND {OK}
            """,
            (subject_id,),
        ).fetchone()[0]

    if not subj:
        await context.bot.send_message(
            chat_id,
            "المادة غير موجودة.",
        )
        return

    # نحفظ مكان المستخدم حتى يعمل زر "التالي" من Reply Keyboard.
    set_state(
        context,
        "questions",
        subject_id=subject_id,
        offset=offset,
        total=total,
    )

    for i, q in enumerate(rows, start=offset + 1):
        if q["image"]:
            path = os.path.join(BASE, q["image"])
            if os.path.exists(path):
                with open(path, "rb") as f:
                    await context.bot.send_photo(chat_id, f)

        options = [clip(o, 100) for o in json.loads(q["options"])][:10]
        title = clip(f"{i}) {q['question']}", 300)

        try:
            await context.bot.send_poll(
                chat_id,
                title,
                options,
                type=PollType.QUIZ,
                is_anonymous=False,
                correct_option_id=q["correct_index"],
                explanation=clip(q["explanation"] or "", 200) or None,
            )
        except Exception as e:
            # سؤال مرفوض من تيليجرام
            log.warning("poll failed q=%s: %s", q["key"], e)

    done = offset + len(rows)

    if done < total:
        next_text = f"⏭️ التالي ({done}/{total})"
        set_state(
            context,
            "questions",
            subject_id=subject_id,
            offset=done,
            total=total,
            next_text=next_text,
        )
        message = f"انتهت دفعة أسئلة «{subj['name']}»"
        keyboard = reply_kb(
            [next_text, "🏠 القائمة الرئيسية"],
            columns=2,
        )
    else:
        set_state(context, "subjects_done")
        message = f"🎉 خلصت كل أسئلة «{subj['name']}»"
        keyboard = reply_kb(
            ["🏠 القائمة الرئيسية"],
            columns=1,
        )

    await context.bot.send_message(
        chat_id,
        message,
        reply_markup=keyboard,
    )


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
            await send_batch(
                message.chat_id,
                context,
                int(subject_id),
                0,
            )
            return

    # ---------------- الأسئلة / التالي ----------------
    if state == "questions":
        if text == "🏠 القائمة الرئيسية":
            await show_years(message, context)
            return

        next_text = context.user_data.get("next_text")
        if next_text and text == next_text:
            subject_id = context.user_data["subject_id"]
            offset = context.user_data["offset"]
            await send_batch(
                message.chat_id,
                context,
                int(subject_id),
                int(offset),
            )
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


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("حط التوكن بمتغير البيئة BOT_TOKEN")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    log.info("bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
