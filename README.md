# AI Assisted Travel Guide — Setup Guide

This is a working Flask MVP: login/register, two recommendation flows
(by destination, by mood), budget estimation scaled by group size,
hotel + food suggestions, weather, a map, a rule-based travel copilot,
and an offline PDF export. It's been tested end-to-end and runs as-is.

Everything below is a command you type into a terminal. Go in order.

## 0. Check Python is installed

Open a terminal (Windows: search "cmd" or "PowerShell"; Mac: search
"Terminal").

```
python3 --version
```

If that fails, try `python --version`. You need 3.9 or newer. If it's
not installed, get it from https://www.python.org/downloads/ — on the
Windows installer, tick "Add Python to PATH" before clicking Install,
or the `python` command won't work afterward.

## 1. Put the project folder somewhere sensible

Unzip `ai_travel_guide.zip` into a folder you'll remember, e.g.
`Documents/ai_travel_guide`. Then in the terminal:

```
cd path/to/ai_travel_guide
```

(Type `cd ` then drag the folder into the terminal window — it fills
in the path for you, on both Mac and Windows.)

## 2. Create a virtual environment

```
python3 -m venv venv
```

**Why:** this creates an isolated Python install just for this
project, so the packages you install next don't clash with anything
else on your laptop.

Activate it:

- Mac/Linux: `source venv/bin/activate`
- Windows (cmd): `venv\Scripts\activate.bat`
- Windows (PowerShell): `venv\Scripts\Activate.ps1`

Your terminal prompt should now start with `(venv)`. Every command
below must be run with that `(venv)` showing — if you close the
terminal, `cd` back into the folder and re-run the activate command.

## 3. Install the dependencies

```
pip install -r requirements.txt
```

**Why:** `requirements.txt` lists exactly which packages the app
needs (Flask for the web server, requests for the weather API call,
fpdf2 for the PDF export) so anyone can recreate the same setup with
one command.

## 4. (Optional but recommended) Get a free weather API key

1. Go to https://openweathermap.org/api and create a free account.
2. Copy your API key from the account dashboard.
3. Set it as an environment variable **in the same terminal**, before
   running the app:
   - Mac/Linux: `export OPENWEATHER_API_KEY=your_key_here`
   - Windows (cmd): `set OPENWEATHER_API_KEY=your_key_here`
   - Windows (PowerShell): `$env:OPENWEATHER_API_KEY="your_key_here"`

If you skip this, the app still runs fine — the weather section just
shows "unavailable" instead of a live temperature.

## 5. Run the app

```
python3 app.py
```

You should see output ending with something like:
```
* Running on http://127.0.0.1:5000
```

Open that address in your browser. You'll land on the login page —
click "Register here" to create an account first.

## 6. Try it end-to-end

1. Register → log in.
2. From the dashboard, pick **"I know my destination"**, choose Goa
   or Manali, a mood, a budget, group size, and nights → **Build my
   plan**.
3. You'll see matched places, transport options (with the cheapest
   or most fitting one highlighted), 3 hotel options, food
   suggestions, a live map, and a total budget.
4. Try the copilot box at the bottom — ask "what's the weather?" or
   "how much will this cost?".
5. Click **Download plan (PDF)** to get an offline copy.
6. Go back to the dashboard and try **"Surprise me by mood"** for the
   sentiment-first flow.

## 7. How to extend it (once the MVP is running)

- **Add more destinations/places:** open `data/places.json` and copy
  an existing destination block, changing the values. No Python
  changes needed — the app reads this file at startup.
- **Add more sentiments:** add the word to the `"sentiments"` list at
  the top of `places.json`, then tag places with it in their
  `"sentiments"` array.
- **Real AI copilot instead of rule-based:** replace the body of the
  `copilot()` function in `app.py` with a call to an LLM API (e.g.
  Anthropic's or OpenAI's), passing the plan as context. Everything
  else in the app stays the same.
- **Real routing/directions:** swap the Google Maps iframe embed in
  `results.html` for the Google Directions API once you have a
  billing-enabled API key.
- **Move off SQLite:** once you have real users, swap the `sqlite3`
  calls in `app.py` for Postgres (e.g. via `psycopg2` or
  `SQLAlchemy`) — the rest of the app doesn't need to change.

## Project structure reference

```
ai_travel_guide/
  app.py                 <- all routes + logic (auth, recommendations, budget, copilot, PDF)
  requirements.txt       <- packages to install
  data/places.json       <- all destinations, places, hotels, transport, food (edit this to expand content)
  templates/              <- HTML pages (Jinja2 templates, rendered by Flask)
  static/css/style.css   <- all styling
  static/js/script.js    <- copilot chat widget behavior
  travel.db               <- created automatically on first run (your local user database)
```
