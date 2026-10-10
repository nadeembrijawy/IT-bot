"""
تخزين الإحصائيات (دائم).

- إذا وُجد DATABASE_URL  -> PostgreSQL خارجي (Neon / Supabase ...) والبيانات ما بتنمحي مع Render.
- إذا ما وُجد             -> ملف SQLite محلي stats.db (للتجربة على الكمبيوتر فقط).

كل الدوال متزامنة (sync)؛ البوت بيستدعيها عبر asyncio.to_thread.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger("qbank.stats")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")
USE_PG = bool(DATABASE_URL)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
        first_seen TEXT, last_seen TEXT)""",
    # كل poll أرسله البوت (عشان نعرف أي سؤال انجاب عليه لما توصل الإجابة)
    """CREATE TABLE IF NOT EXISTS poll_map(
        poll_id TEXT PRIMARY KEY, user_id BIGINT, batch_id TEXT, question_key TEXT,
        subject_id INTEGER, subject_name TEXT, year INTEGER, semester INTEGER,
        correct_index INTEGER, sent_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_pm_batch ON poll_map(batch_id)",
    # إجابات الطلاب
    """CREATE TABLE IF NOT EXISTS answers(
        poll_id TEXT, user_id BIGINT, question_key TEXT,
        subject_id INTEGER, subject_name TEXT, year INTEGER, semester INTEGER,
        chosen INTEGER, is_correct INTEGER, answered_at TEXT,
        PRIMARY KEY (poll_id, user_id))""",
    "CREATE INDEX IF NOT EXISTS ix_ans_user_subj ON answers(user_id, subject_id)",
    # أحداث التصفح: اختيار سنة / فتح مادة / زر التالي
    """CREATE TABLE IF NOT EXISTS events(
        user_id BIGINT, kind TEXT, year INTEGER, semester INTEGER,
        subject_id INTEGER, subject_name TEXT, ts TEXT)""",
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect():
    if USE_PG:
        import psycopg
        return psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
    con = sqlite3.connect(LOCAL_PATH, timeout=10)
    con.isolation_level = None  # autocommit
    return con


def _q(sql):
    return sql.replace("?", "%s") if USE_PG else sql


def execute(sql, params=()):
    con = _connect()
    try:
        con.execute(_q(sql), params)
    finally:
        con.close()


def fetch(sql, params=()):
    con = _connect()
    try:
        cur = con.execute(_q(sql), params)
        return cur.fetchall()
    finally:
        con.close()


def init():
    con = _connect()
    try:
        for s in SCHEMA:
            con.execute(s)
        # نظّف جدول poll_map من الأقدم من 60 يوم
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        con.execute(_q("DELETE FROM poll_map WHERE sent_at < ?"), (old,))
    finally:
        con.close()
    log.info("stats storage ready (%s)", "postgres" if USE_PG else "local sqlite: " + LOCAL_PATH)


# ---------------------------------------------------------------- كتابة
def touch_user(user_id, username, first_name):
    t = now()
    execute(
        "INSERT INTO users(user_id, username, first_name, first_seen, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen, "
        "username=excluded.username, first_name=excluded.first_name",
        (user_id, username, first_name, t, t))


def log_event(user_id, kind, year=None, semester=None, subject_id=None, subject_name=None):
    execute("INSERT INTO events(user_id, kind, year, semester, subject_id, subject_name, ts) VALUES(?,?,?,?,?,?,?)",
            (user_id, kind, year, semester, subject_id, subject_name, now()))


def save_poll(poll_id, user_id, batch_id, question_key, subject_id, subject_name, year, semester, correct_index):
    execute(
        "INSERT INTO poll_map(poll_id, user_id, batch_id, question_key, subject_id, subject_name, year, semester, "
        "correct_index, sent_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(poll_id) DO NOTHING",
        (poll_id, user_id, batch_id, question_key, subject_id, subject_name, year, semester, correct_index, now()))


def record_answer(poll_id, user_id, chosen):
    """يسجّل إجابة. يرجع (batch_id, subject_id, is_correct) أو None إذا الـ poll غير معروف."""
    rows = fetch("SELECT batch_id, question_key, subject_id, subject_name, year, semester, correct_index "
                 "FROM poll_map WHERE poll_id=?", (poll_id,))
    if not rows:
        return None
    batch_id, key, sid, sname, year, sem, correct = rows[0]
    ok = 1 if chosen == correct else 0
    execute(
        "INSERT INTO answers(poll_id, user_id, question_key, subject_id, subject_name, year, semester, chosen, "
        "is_correct, answered_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(poll_id, user_id) DO NOTHING",
        (poll_id, user_id, key, sid, sname, year, sem, chosen, ok, now()))
    return batch_id, sid, ok


# ---------------------------------------------------------------- قراءة (نتيجة الطالب)
def batch_result(user_id, batch_id):
    """(عدد أسئلة الدفعة, صح, غلط)"""
    total = fetch("SELECT COUNT(*) FROM poll_map WHERE batch_id=?", (batch_id,))[0][0]
    r = fetch(
        "SELECT COALESCE(SUM(a.is_correct),0), COUNT(*) FROM answers a "
        "JOIN poll_map p ON p.poll_id=a.poll_id WHERE p.batch_id=? AND a.user_id=?", (batch_id, user_id))[0]
    right, answered = int(r[0]), int(r[1])
    return total, right, answered - right


def subject_result(user_id, subject_id):
    """(صح, غلط) للمادة كلها؛ آخر إجابة لكل سؤال فقط (إذا أعاد الطالب السؤال ما يتكرر)."""
    rows = fetch("SELECT question_key, is_correct FROM answers WHERE user_id=? AND subject_id=? "
                 "ORDER BY answered_at", (user_id, subject_id))
    last = {k: c for k, c in rows}
    right = sum(1 for c in last.values() if c)
    return right, len(last) - right


# ---------------------------------------------------------------- قراءة (تحليل الأدمن)
def _pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def overview():
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    users = fetch("SELECT COUNT(*) FROM users")[0][0]
    u7 = fetch("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (week,))[0][0]
    u1 = fetch("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (day,))[0][0]
    tot = fetch("SELECT COUNT(*), COALESCE(SUM(is_correct),0) FROM answers")[0]
    n, right = int(tot[0]), int(tot[1])

    by_year = fetch("SELECT year, COUNT(*), SUM(is_correct), COUNT(DISTINCT user_id) FROM answers "
                    "GROUP BY year ORDER BY COUNT(*) DESC")
    opens_year = fetch("SELECT year, COUNT(*) FROM events WHERE kind='year' GROUP BY year ORDER BY COUNT(*) DESC")
    top_subj = fetch("SELECT subject_name, year, COUNT(*), SUM(is_correct), COUNT(DISTINCT user_id) FROM answers "
                     "GROUP BY subject_id, subject_name, year ORDER BY COUNT(*) DESC LIMIT 10")
    opens_subj = fetch("SELECT subject_name, year, COUNT(*) FROM events WHERE kind='subject' "
                       "GROUP BY subject_id, subject_name, year ORDER BY COUNT(*) DESC LIMIT 5")
    hardest = fetch("SELECT question_key, subject_name, COUNT(*) n, SUM(is_correct) r FROM answers "
                    "GROUP BY question_key, subject_name HAVING COUNT(*) >= 5 "
                    "ORDER BY (1.0*SUM(is_correct)/COUNT(*)) ASC, COUNT(*) DESC LIMIT 8")

    L = ["📊 إحصائيات البوت", "",
         f"👥 المستخدمين: {users}  (آخر 7 أيام: {u7} | آخر 24 ساعة: {u1})",
         f"📝 إجمالي الإجابات: {n}  —  ✅ {right}  ❌ {n - right}  ({_pct(right, n)} صح)", ""]
    L.append("📅 حسب السنة (عدد الإجابات | نسبة الصح | طلاب):")
    for y, c, r, u in by_year:
        L.append(f"  • سنة {y}: {c} | {_pct(int(r or 0), c)} | {u}")
    if opens_year:
        L.append("")
        L.append("👆 مرات اختيار السنة: " + "، ".join(f"سنة {y}: {c}" for y, c in opens_year))
    L += ["", "📚 أكتر المواد حلاً:"]
    for i, (name, y, c, r, u) in enumerate(top_subj, 1):
        L.append(f"  {i}. {name} (سنة {y}) — {c} إجابة | {_pct(int(r or 0), c)} صح | {u} طالب")
    if opens_subj:
        L += ["", "🔎 أكتر المواد فتحاً:"]
        for name, y, c in opens_subj:
            L.append(f"  • {name} (سنة {y}): {c}")
    if hardest:
        L += ["", "⚠️ أصعب الأسئلة (≥5 إجابات) — يمكن الجواب بالبنك غلط، راجعها:"]
        for key, name, c, r in hardest:
            L.append(f"  • {key} [{name}]: {_pct(int(r or 0), c)} صح من {c}")
    return "\n".join(L)


def export_csv():
    import csv
    import io
    rows = fetch("SELECT a.answered_at, a.user_id, u.username, a.year, a.semester, a.subject_name, a.question_key, "
                 "a.chosen, a.is_correct FROM answers a LEFT JOIN users u ON u.user_id=a.user_id "
                 "ORDER BY a.answered_at")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["answered_at_utc", "user_id", "username", "year", "semester", "subject", "question_key",
                "chosen_index", "is_correct"])
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM عشان Excel يقرأ العربي
