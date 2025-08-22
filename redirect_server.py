from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import hashlib, sqlite3, time
import os

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")


app = FastAPI(title="Real Estate SmartLinks Redirect")

# Allow Streamlit (and local dev) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Streamlit domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DB setup (single source of truth) ---
conn = sqlite3.connect("realestate_links.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT,
    short_code TEXT UNIQUE,
    clicks INTEGER DEFAULT 0,
    created_at TEXT
)
""")
conn.commit()

def shorten_url(seed: str) -> str:
    # 6-char code; retry on rare collision
    while True:
        code = hashlib.md5((seed + str(time.time())).encode()).hexdigest()[:6]
        c.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
        if not c.fetchone():
            return code

# --- Schemas ---
class CreateLinkIn(BaseModel):
    original_url: str

# --- API: create a link ---
@app.post("/api/links")
def create_link(data: CreateLinkIn):
    code = shorten_url(data.original_url)
    created = datetime.now().isoformat()
    c.execute(
        "INSERT INTO links (original_url, short_code, created_at) VALUES (?,?,?)",
        (data.original_url, code, created),
    )
    conn.commit()
    short_url = f"{PUBLIC_BASE_URL}/{code}"
    return {"original_url": data.original_url, "short_code": code, "short_url": short_url, "created_at": created, "clicks": 0}

# --- API: list links ---
@app.get("/api/links")
def list_links():
    rows = c.execute(
        "SELECT original_url, short_code, clicks, created_at FROM links ORDER BY id DESC"
    ).fetchall()
    out = []
    for orig, code, clicks, created in rows:
        out.append({
            "original_url": orig,
            "short_code": code,
            "clicks": clicks,
            "created_at": created,
            "short_url": f"{base_url_hint()}/{code}",
        })
    return out

# --- Redirect endpoint (tracks clicks) ---
@app.get("/{short_code}")
def redirect_short_link(short_code: str):
    c.execute("SELECT original_url, clicks FROM links WHERE short_code = ?", (short_code,))
    row = c.fetchone()
    if row:
        original_url, clicks = row
        c.execute("UPDATE links SET clicks = ? WHERE short_code = ?", (clicks + 1, short_code))
        conn.commit()
        return RedirectResponse(url=original_url)
    else:
        raise HTTPException(status_code=404, detail="SmartLink not found")

# Helper: derive base URL from request headers, fallback to localhost
def base_url_hint():
    # In production, set an env var and use that instead if you prefer
    return "http://127.0.0.1:8000"

