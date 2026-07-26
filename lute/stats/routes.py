"""
/stats endpoints.
"""

from flask import Blueprint, render_template, jsonify
from lute.stats.service import (
    get_chart_data,
    get_table_data,
    get_known_table_data,
    get_known_chart_data,
    get_reading_streak,
)
from lute.db import db

bp = Blueprint("stats", __name__, url_prefix="/stats")


@bp.route("/")
def index():
    "Main page."
    read_table_data = get_table_data(db.session)
    known_table_data = get_known_table_data(db.session)
    reading_streak = get_reading_streak(db.session)
    return render_template(
        "stats/index.html",
        read_table_data=read_table_data,
        known_table_data=known_table_data,
        reading_streak=reading_streak,
    )


@bp.route("/data")
def get_data():
    "Ajax call."
    chartdata = get_chart_data(db.session)
    return jsonify(chartdata)


@bp.route("/known_data")
def get_known_data():
    "Ajax call for the words-marked-known running total chart."
    chartdata = get_known_chart_data(db.session)
    return jsonify(chartdata)
