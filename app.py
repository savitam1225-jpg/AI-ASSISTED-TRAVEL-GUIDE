"""
AI Assisted Travel Guide - main Flask application.

WHY this file is structured this way:
- Everything routes through a handful of small, testable functions
  (build_plan, estimate_budget, recommend_local_transport, ask_copilot)
  instead of stuffing logic into the route handlers. That makes each
  piece easy to read, fix, and later replace (e.g. swap the copilot
  rule-engine for a real LLM call) without touching the rest.
- Data (places, hotels, transport, food) lives in data/places.json,
  NOT hard-coded in Python. That means adding a new city or place is
  a JSON edit, not a code change.
"""

import json
import math
import os
import sqlite3
from datetime import datetime

import requests
from flask import (Flask, g, redirect, render_template, request,
                    send_file, session, url_for, flash, jsonify)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "travel.db")
DATA_PATH = os.path.join(BASE_DIR, "data", "places.json")

# Get a free key at https://openweathermap.org/api and set it as an
# environment variable before running the app:
#   Windows (cmd):   set OPENWEATHER_API_KEY=xxxx
#   Mac/Linux:       export OPENWEATHER_API_KEY=xxxx
# The app still works without it - weather just shows "unavailable".
WEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")

app = Flask(__name__)
# Secret key signs the session cookie. In a real deployment this must
# come from an environment variable, not be hard-coded - anyone who
# has this string can forge login sessions.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-this-key")

with open(DATA_PATH, encoding="utf-8") as f:
    PLACES_DATA = json.load(f)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    """One SQLite connection per request, reused if called twice."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the users table if it doesn't exist yet. Called once at startup."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

def login_required(view):
    """Decorator: redirect to /login if no user_id in session."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not name or not email or not password:
            flash("Please fill in every field.")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            flash("An account with that email already exists.")
            return render_template("register.html")

        # NEVER store plain-text passwords - generate_password_hash salts
        # and hashes it, so even if the database leaks, passwords aren't
        # directly readable.
        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, datetime.utcnow().isoformat()),
        )
        db.commit()
        flash("Account created. Please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

def estimate_budget(destination_key, budget_tier, people, nights):
    """
    Returns a dict with an itemised cost estimate, scaled by group size.

    WHY separate from the route: this is pure logic with no Flask
    dependency, so it's easy to unit test on its own later.
    """
    dest = PLACES_DATA["destinations"][destination_key]
    days = nights + 1

    # --- inter-city transport: pick the mode matching the budget tier ---
    tier_preference = {
        "low": ["Bus", "Train"],
        "medium": ["Train", "Private Cab"],
        "high": ["Flight", "Private Cab"],
    }
    transport_options = []
    for opt in dest["transport"]:
        if "price_per_person" in opt:
            cost = opt["price_per_person"] * people
        else:  # priced per vehicle -> round up number of vehicles needed
            vehicles_needed = math.ceil(people / opt["capacity"])
            cost = opt["price_per_vehicle"] * vehicles_needed
        transport_options.append({**opt, "total_cost": cost})

    recommended_transport = None
    for pref in tier_preference[budget_tier]:
        for opt in transport_options:
            if pref.lower() in opt["mode"].lower():
                recommended_transport = opt
                break
        if recommended_transport:
            break
    if recommended_transport is None:
        recommended_transport = min(transport_options, key=lambda o: o["total_cost"])

    # --- stay: 2 people per room, pick hotel matching budget tier ---
    rooms_needed = math.ceil(people / 2)
    stay_tier = {"low": "Budget", "medium": "Mid-range", "high": "Luxury"}[budget_tier]
    stay_options = []
    for opt in dest["stay"]:
        cost = opt["price_per_night"] * nights * rooms_needed
        stay_options.append({**opt, "rooms": rooms_needed, "total_cost": cost})
    recommended_stay = next((o for o in stay_options if o["type"] == stay_tier), stay_options[0])

    # --- local transport: pick by group size, then by budget tier ---
    local_transport = recommend_local_transport(dest, people, budget_tier, days)

    # --- food: flat per-person, per-day estimate by tier ---
    food_rate = {"low": 400, "medium": 800, "high": 1500}[budget_tier]
    food_cost = food_rate * people * days

    total = (
        recommended_transport["total_cost"]
        + recommended_stay["total_cost"]
        + local_transport["total_cost"]
        + food_cost
    )

    return {
        "days": days,
        "transport_options": transport_options,
        "recommended_transport": recommended_transport,
        "stay_options": stay_options,
        "recommended_stay": recommended_stay,
        "local_transport": local_transport,
        "food_cost": food_cost,
        "food_rate_per_person_per_day": food_rate,
        "total": total,
    }


def recommend_local_transport(dest, people, budget_tier, days):
    """
    Picks a within-destination vehicle based on group size AND budget,
    e.g. a couple on a low budget gets a bike; a family of 6 gets two
    cars with a driver.
    """
    options_by_mode = {o["mode"]: o for o in dest["local_transport"]}

    if people <= 2 and budget_tier == "low":
        mode = "Bike Rental"
    elif people <= 4 and budget_tier != "high":
        mode = "Car Rental (self-drive)" if budget_tier == "low" else "Taxi / Cab (with driver)"
    elif people <= 4:
        mode = "Private Driver with Car"
    else:
        mode = "Private Driver with Car"

    chosen = options_by_mode.get(mode, list(options_by_mode.values())[0])
    vehicles_needed = math.ceil(people / chosen["capacity"])
    cost = chosen["price_per_day"] * days * vehicles_needed

    return {**chosen, "vehicles_needed": vehicles_needed, "total_cost": cost}


def filter_places(destination_key=None, sentiment=None, budget_tier=None):
    """
    Core matching function used by BOTH recommendation flows (by
    destination, and by sentiment-first). One function, two entry
    points - avoids duplicating filter logic.
    """
    results = []
    destinations = (
        {destination_key: PLACES_DATA["destinations"][destination_key]}
        if destination_key
        else PLACES_DATA["destinations"]
    )

    for dest_key, dest in destinations.items():
        for place in dest["places"]:
            if sentiment and sentiment not in place["sentiments"]:
                continue
            if budget_tier and place["budget"] != budget_tier:
                continue
            results.append({**place, "destination": dest_key, "destination_title": dest["title"]})

    # Fallback: if nothing matched exactly, relax the budget filter first,
    # since sentiment is the primary intent the user expressed.
    if not results and (sentiment or budget_tier):
        for dest_key, dest in destinations.items():
            for place in dest["places"]:
                if sentiment and sentiment not in place["sentiments"]:
                    continue
                results.append({**place, "destination": dest_key, "destination_title": dest["title"]})

    return results


def get_weather(city_query):
    if not WEATHER_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city_query, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5,
        )
        data = resp.json()
        if resp.status_code != 200:
            return None
        return {
            "temp": round(data["main"]["temp"]),
            "feels_like": round(data["main"]["feels_like"]),
            "description": data["weather"][0]["description"].title(),
            "humidity": data["main"]["humidity"],
        }
    except requests.RequestException:
        return None


def build_plan(destination_key, sentiment, budget_tier, people, nights):
    dest = PLACES_DATA["destinations"][destination_key]
    matched_places = filter_places(destination_key, sentiment, budget_tier)[:10]
    budget = estimate_budget(destination_key, budget_tier, people, nights)
    weather = get_weather(dest["weather_city"])

    return {
        "destination": destination_key,
        "destination_title": dest["title"],
        "sentiment": sentiment,
        "budget_tier": budget_tier,
        "people": people,
        "nights": nights,
        "places": matched_places,
        "food": dest["food"],
        "weather": weather,
        "budget": budget,
        "map_query": dest["title"],
    }


# ---------------------------------------------------------------------------
# App routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard") if session.get("user_id") else url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        destinations=PLACES_DATA["destinations"],
        sentiments=PLACES_DATA["sentiments"],
    )


@app.route("/plan/by-destination", methods=["GET", "POST"])
@login_required
def plan_by_destination():
    if request.method == "POST":
        destination_key = request.form["destination"]
        sentiment = request.form.get("sentiment") or None
        budget_tier = request.form["budget"]
        people = max(1, int(request.form.get("people", 1)))
        nights = max(1, int(request.form.get("nights", 3)))

        plan = build_plan(destination_key, sentiment, budget_tier, people, nights)
        # Only the small parameter set is kept in the session (not the
        # whole plan) - Flask's default session is a signed cookie with
        # a ~4KB limit, and the full plan (10 place descriptions, hotel
        # lists, etc.) would be too big. /copilot and /download-plan
        # rebuild the plan from these params instead.
        session["last_plan_params"] = {
            "destination_key": destination_key,
            "sentiment": sentiment,
            "budget_tier": budget_tier,
            "people": people,
            "nights": nights,
        }
        return render_template("results.html", plan=plan)

    return render_template(
        "destination_form.html",
        destinations=PLACES_DATA["destinations"],
        sentiments=PLACES_DATA["sentiments"],
    )


@app.route("/plan/by-sentiment", methods=["GET", "POST"])
@login_required
def plan_by_sentiment():
    if request.method == "POST":
        sentiment = request.form["sentiment"]
        budget_tier = request.form["budget"]
        people = max(1, int(request.form.get("people", 1)))
        nights = max(1, int(request.form.get("nights", 3)))

        matches = filter_places(None, sentiment, budget_tier)
        return render_template(
            "sentiment_results.html",
            matches=matches,
            sentiment=sentiment,
            budget=budget_tier,
            people=people,
            nights=nights,
        )

    return render_template("sentiment_form.html", sentiments=PLACES_DATA["sentiments"])


@app.route("/copilot", methods=["POST"])
@login_required
def copilot():
    """
    Rule-based Q&A over the last generated plan. This is intentionally
    NOT calling an external LLM - that keeps the project runnable with
    zero extra API keys or cost today. Swap the body of this function
    for a real model call later without touching any other route.
    """
    question = request.json.get("question", "").lower()
    params = session.get("last_plan_params")
    plan = build_plan(**params) if params else None

    if not plan:
        return jsonify({"answer": "Build a travel plan first, then ask me about it!"})

    if "weather" in question:
        w = plan.get("weather")
        answer = (
            f"{plan['destination_title']} is currently {w['temp']}°C, {w['description']}."
            if w
            else "Weather data isn't available right now - add an OPENWEATHER_API_KEY to enable it."
        )
    elif "hotel" in question or "stay" in question:
        s = plan["budget"]["recommended_stay"]
        answer = f"For your budget, {s['name']} (₹{s['price_per_night']}/night, {s['type']}) is recommended."
    elif "food" in question or "eat" in question:
        dishes = ", ".join(plan["food"]["dishes"][:3])
        answer = f"Try {dishes} in {plan['destination_title']}."
    elif "budget" in question or "cost" in question or "price" in question:
        answer = f"Your estimated total for {plan['people']} people, {plan['nights']} nights is ₹{plan['budget']['total']:,}."
    elif "transport" in question or "reach" in question or "travel" in question:
        t = plan["budget"]["recommended_transport"]
        answer = f"Recommended: {t['mode']} (₹{t['total_cost']:,} total for your group)."
    else:
        answer = (
            "I can answer questions about weather, hotels, food, budget or transport "
            "for your current plan - try asking about one of those!"
        )

    return jsonify({"answer": answer})


@app.route("/download-plan")
@login_required
def download_plan():
    """Generates a simple offline-readable PDF from the last plan in session."""
    from fpdf import FPDF

    params = session.get("last_plan_params")
    plan = build_plan(**params) if params else None
    if not plan:
        flash("Generate a plan first.")
        return redirect(url_for("dashboard"))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Travel Plan: {plan['destination_title']}", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Group size: {plan['people']}  |  Nights: {plan['nights']}", ln=True)
    pdf.cell(0, 8, f"Estimated total budget: Rs {plan['budget']['total']:,}", ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Places to visit", ln=True)
    pdf.set_font("Helvetica", "", 10)
    for place in plan["places"]:
        pdf.multi_cell(0, 6, f"- {place['name']}: {place['desc']}")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Travel & stay", ln=True)
    pdf.set_font("Helvetica", "", 10)
    t = plan["budget"]["recommended_transport"]
    s = plan["budget"]["recommended_stay"]
    pdf.multi_cell(0, 6, f"Transport: {t['mode']} - Rs {t['total_cost']:,}")
    pdf.multi_cell(0, 6, f"Stay: {s['name']} ({s['type']}) - Rs {s['total_cost']:,}")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Food to try", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, ", ".join(plan["food"]["dishes"]))

    out_path = os.path.join(BASE_DIR, "travel_plan.pdf")
    pdf.output(out_path)
    return send_file(out_path, as_attachment=True, download_name=f"{plan['destination']}_travel_plan.pdf")


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
