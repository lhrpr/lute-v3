"Stats service test."

from datetime import datetime, timedelta
from sqlalchemy import text
from lute.models.book import WordsRead
from lute.db import db
from lute.stats.service import (
    get_chart_data,
    get_table_data,
    get_known_table_data,
    get_known_chart_data,
    get_reading_streak,
)
from tests.utils import make_text, add_terms


def make_read_text(lang, content, readdate):
    "Make and save a text."
    t = make_text(content, content, lang)
    # t.read_date = readdate
    db.session.add(t)
    db.session.commit()

    if readdate is None:
        return

    wr = WordsRead(t, readdate, t.word_count)
    db.session.add(wr)
    db.session.commit()


def test_get_chart_data(spanish, english, app_context):
    "Smoke test."
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    daybefore = today - timedelta(days=2)

    make_read_text(spanish, "Yo tengo un gato.", today)
    make_read_text(spanish, "Ella esta aqui.", yesterday)
    make_read_text(spanish, "Nuevo text no leido.", None)
    make_read_text(english, "Yo yo.", today)

    expected = {
        "Spanish": [
            {
                "readdate": daybefore.strftime("%Y-%m-%d"),
                "wordcount": 0,
                "runningTotal": 0,
            },
            {
                "readdate": yesterday.strftime("%Y-%m-%d"),
                "wordcount": 3,
                "runningTotal": 3,
            },
            {"readdate": today.strftime("%Y-%m-%d"), "wordcount": 4, "runningTotal": 7},
        ],
        "English": [
            {
                "readdate": yesterday.strftime("%Y-%m-%d"),
                "wordcount": 0,
                "runningTotal": 0,
            },
            {"readdate": today.strftime("%Y-%m-%d"), "wordcount": 2, "runningTotal": 2},
        ],
    }
    assert get_chart_data(db.session) == expected


def test_get_table_data(spanish, english, app_context):
    "Smoke test."
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    make_read_text(spanish, "Yo tengo un gato.", today)
    make_read_text(spanish, "Ella esta aqui.", yesterday)
    make_read_text(spanish, "Nuevo text no leido.", None)
    make_read_text(english, "Yo yo.", today)

    expected = [
        {
            "name": "English",
            "counts": {"day": 2, "week": 2, "month": 2, "year": 2, "total": 2},
        },
        {
            "name": "Spanish",
            "counts": {"day": 4, "week": 7, "month": 7, "year": 7, "total": 7},
        },
    ]
    actual = get_table_data(db.session)
    assert actual == expected


def _mark_known(term, status, days_ago=0):
    """
    Set a term's status (firing the WoStatusChanged trigger), then
    optionally backdate WoStatusChanged by days_ago.

    WoStatusChanged is stored in UTC, so backdating from utcnow keeps the
    value consistent with the 'localtime' conversion used by the service.
    """
    term.status = status
    db.session.add(term)
    db.session.commit()
    if days_ago > 0:
        backdate = (datetime.utcnow() - timedelta(days=days_ago)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        db.session.execute(
            text("update words set WoStatusChanged = :d where WoID = :id"),
            {"d": backdate, "id": term.id},
        )
        db.session.commit()


def test_get_known_table_data(spanish, english, app_context):
    "Words marked Learned (5) or Well Known (99), bucketed by status change date."
    sp = add_terms(
        spanish,
        ["hoy", "ahora", "semana", "mes", "ano", "viejo", "aprendiendo", "ignorado"],
    )
    # 2 known today, then one each in the week/month/year/total buckets.
    _mark_known(sp[0], 5)  # today
    _mark_known(sp[1], 99)  # today
    _mark_known(sp[2], 5, days_ago=3)  # within last week
    _mark_known(sp[3], 5, days_ago=15)  # within last month
    _mark_known(sp[4], 99, days_ago=100)  # within last year
    _mark_known(sp[5], 5, days_ago=800)  # all time only
    # Not "known" - should be excluded entirely.
    _mark_known(sp[6], 3)  # learning
    _mark_known(sp[7], 98)  # ignored

    en = add_terms(english, ["today", "learning"])
    _mark_known(en[0], 5)  # today
    _mark_known(en[1], 4)  # excluded

    expected = [
        {
            "name": "English",
            "counts": {"day": 1, "week": 1, "month": 1, "year": 1, "total": 1},
        },
        {
            "name": "Spanish",
            "counts": {"day": 2, "week": 3, "month": 4, "year": 5, "total": 6},
        },
    ]
    actual = get_known_table_data(db.session)
    assert actual == expected


def test_get_known_chart_data(spanish, app_context):
    "Running total of words marked known, with a leading zero-point day."
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    daybefore = today - timedelta(days=2)

    sp = add_terms(spanish, ["ayer", "hoy1", "hoy2", "aprendiendo"])
    _mark_known(sp[0], 5, days_ago=1)  # yesterday
    _mark_known(sp[1], 5)  # today
    _mark_known(sp[2], 99)  # today
    _mark_known(sp[3], 3)  # learning - excluded

    expected = {
        "Spanish": [
            {
                "readdate": daybefore.strftime("%Y-%m-%d"),
                "wordcount": 0,
                "runningTotal": 0,
            },
            {
                "readdate": yesterday.strftime("%Y-%m-%d"),
                "wordcount": 1,
                "runningTotal": 1,
            },
            {"readdate": today.strftime("%Y-%m-%d"), "wordcount": 2, "runningTotal": 3},
        ],
    }
    assert get_known_chart_data(db.session) == expected


def test_get_data_works_when_nothing_read(app_context):
    "Nothing read should still be ok, empty chart."
    assert not get_chart_data(db.session), "nothing present"
    assert not get_table_data(db.session), "nothing"
    assert not get_known_table_data(db.session), "nothing marked known"
    assert not get_known_chart_data(db.session), "nothing marked known"


def test_get_reading_streak(spanish, app_context):
    "Test reading streak calculation."
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    day_before_yesterday = today - timedelta(days=2)

    assert get_reading_streak(db.session) == 0

    make_read_text(spanish, "Ella esta aqui.", today)
    assert get_reading_streak(db.session) == 1

    make_read_text(spanish, "Nuevo text.", yesterday)
    assert get_reading_streak(db.session) == 2

    make_read_text(spanish, "Otro text.", day_before_yesterday)
    assert get_reading_streak(db.session) == 3
