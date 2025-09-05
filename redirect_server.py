import os
import sqlite3
from datetime import datetime, timezone

import stripe
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from user_agents import parse as parse_ua

# === Config ===
DB_PATH = os.getenv("DB_PATH", "smartlinks.db")
MAX_FREE_LINKS = int(os.getenv("MAX_FREE_LINKS", "3"))
stripe.api_key = os.getenv("STRIPE_API_KEY")
PRICE_ID = os.getenv("STRIPE_PRICE_ID")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# === DB helpers ===
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS links (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_token TEXT,
      original_url TEXT NOT NULL,
      short_code TEXT UNIQUE,
      created_at TEXT,
      plan TEXT DEFAULT 'free'
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clicks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      link_id INTEGER NOT NULL,
      ts TEXT NOT NULL,
      ip TEXT,
      user_agent TEXT,
      device_type TEXT,
      FOREIGN KEY(link_id) REFERENCES links(id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE,
      owner_token TEXT UNIQUE,
      plan TEXT DEFAULT 'free',
      created_at TEXT,
      updated_at TEXT
    )""")

    conn.commit()
    conn.close()

init_db()

# === helpers ===
def get_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"

def get_device_info(request: Request):
    ua_str = request.headers.get("user-agent", "")
    ua = parse_ua(ua_str)
    if ua.is_tablet:
        device = "tablet"
    elif ua.is_mobile:
        device = "mobile"
    elif ua.is_pc:
        device = "desktop"
    elif ua.is_bot:
        device = "bot"
    else:
        device = "unknown"
    return ua_str[:512], device

# === FastAPI app ===
app = FastAPI()

# === Models ===
class CreateLinkIn(BaseModel):
    owner_token: str
    original_url: str
    email: str | None = None

class CheckoutIn(BaseModel):
    owner_token: str
    email: str | None = None

# === Endpoints ===
@app.post("/api/links")
async def create_link(payload: CreateLinkIn):
    owner = payload.owner_token.strip()
    url = payload.original_url.strip()
    email = (payload.email or "").strip().lower()
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    cur = conn.cursor()

    # ensure user row exists
    if email:
        cur.execute("SELECT id FROM users WHERE email = ? OR owner_token = ?", (email, owner))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO users (email, owner_token, plan, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (email, owner, 'free', now, now))
        else:
            cur.execute("UPDATE users SET email=?, owner_token=?, updated_at=? WHERE id=?",
                        (email, owner, now, row["id"]))
        conn.commit()

    # check plan
    cur.execute("SELECT plan FROM users WHERE owner_token=? OR email=?", (owner, email))
    row = cur.fetchone()
    plan = row["plan"] if row else "free"

    if plan != "pro":
        cur.execute("SELECT COUNT(*) AS c FROM links WHERE owner_token=?", (owner,))
        cnt = cur.fetchone()["c"]
        if cnt >= MAX_FREE_LINKS:
            raise HTTPException(status_code=402, detail="Free tier limit reached. Upgrade to Pro.")

    short_code = hex(abs(hash(f"{owner}:{url}:{now}")))[2:10]
    cur.execute("INSERT INTO links (owner_token, original_url, short_code, created_at, plan) VALUES (?,?,?,?,?)",
                (owner, url, short_code, now, plan))
    conn.commit()
    conn.close()
    return {"short_code": short_code, "plan": plan}

@app.get("/r/{short_code}")
async def redirect(short_code: str, request: Request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, original_url FROM links WHERE short_code=?", (short_code,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link not found")

    link_id = row["id"]
    ip = get_ip(request)
    ua_str, device = get_device_info(request)
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("INSERT INTO clicks (link_id, ts, ip, user_agent, device_type) VALUES (?,?,?,?,?)",
                (link_id, now, ip, ua_str, device))
    conn.commit()
    conn.close()
    return RedirectResponse(url=row["original_url"], status_code=302)

@app.post("/api/stripe/checkout")
async def create_checkout_session(payload: CheckoutIn):
    if not PRICE_ID:
        raise HTTPException(status_code=500, detail="Stripe price not set")
    metadata = {"owner_token": payload.owner_token}
    if payload.email:
        metadata["email"] = payload.email
    session = stripe.checkout.Session.create(
        mode="subscription",
        success_url=f"{PUBLIC_BASE_URL}/api/stripe/success",
        cancel_url=f"{PUBLIC_BASE_URL}/api/stripe/cancel",
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        metadata=metadata,
        customer_email=payload.email if payload.email else None,
        allow_promotion_codes=True
    )
    return {"checkout_url": session.url}

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="stripe-signature")):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        metadata = data.get("metadata", {}) or {}
        owner_token = metadata.get("owner_token")
        email = metadata.get("email") or data.get("customer_details", {}).get("email")
        _upgrade_user(owner_token, email)

    return JSONResponse({"received": True})

def _upgrade_user(owner_token: str | None, email: str | None):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    if owner_token:
        cur.execute("SELECT id FROM users WHERE owner_token=?", (owner_token,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO users (email, owner_token, plan, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (email, owner_token, 'pro', now, now))
        else:
            cur.execute("UPDATE users SET plan='pro', updated_at=? WHERE owner_token=?", (now, owner_token))
        cur.execute("UPDATE links SET plan='pro' WHERE owner_token=?", (owner_token,))
    elif email:
        cur.execute("SELECT id FROM users WHERE email=?", (email,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO users (email, plan, created_at, updated_at) VALUES (?,?,?,?)",
                        (email, 'pro', now, now))
        else:
            cur.execute("UPDATE users SET plan='pro', updated_at=? WHERE email=?", (now, email))
    conn.commit()
    conn.close()
