import os, sqlite3, json
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

# third-party (installed via requirements.txt)
try:
    import stripe  # optional in "safe mode" until keys exist
except Exception:
    stripe = None

try:
    from user_agents import parse as parse_ua
except Exception:
    def parse_ua(_):  # fallback if lib missing
        class U: is_tablet=is_mobile=is_pc=is_bot=False
        return U()

# ==== CONFIG ====
DB_PATH = os.getenv("DB_PATH", "smartlinks.db")
MAX_FREE_LINKS = int(os.getenv("MAX_FREE_LINKS", "3"))

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

if stripe and STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# ==== DB ====
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

# ==== APP ====
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # OK for MVP; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
init_db()

# ==== helpers ====
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff: return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri: return xri.strip()
    return request.client.host if request.client else "unknown"

def get_device_info(request: Request):
    ua_str = request.headers.get("user-agent", "")[:512]
    ua = parse_ua(ua_str)
    if getattr(ua, "is_tablet", False): device = "tablet"
    elif getattr(ua, "is_mobile", False): device = "mobile"
    elif getattr(ua, "is_pc", False): device = "desktop"
    elif getattr(ua, "is_bot", False): device = "bot"
    else: device = "unknown"
    return ua_str, device

def upgrade_user(owner_token: str | None, email: str | None):
    conn = get_db(); cur = conn.cursor(); n = now_iso()
    if owner_token:
        cur.execute("SELECT id FROM users WHERE owner_token=?", (owner_token,))
        r = cur.fetchone()
        if r is None:
            cur.execute("INSERT INTO users (email, owner_token, plan, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (email, owner_token, 'pro', n, n))
        else:
            cur.execute("UPDATE users SET plan='pro', email=COALESCE(?,email), updated_at=? WHERE owner_token=?",
                        (email, n, owner_token))
        cur.execute("UPDATE links SET plan='pro' WHERE owner_token=?", (owner_token,))
    elif email:
        cur.execute("SELECT id FROM users WHERE email=?", (email,))
        r = cur.fetchone()
        if r is None:
            cur.execute("INSERT INTO users (email, plan, created_at, updated_at) VALUES (?,?,?,?)",
                        (email, 'pro', n, n))
        else:
            cur.execute("UPDATE users SET plan='pro', updated_at=? WHERE email=?", (n, email))
    conn.commit(); conn.close()

# ==== models ====
class CreateLinkIn(BaseModel):
    owner_token: str
    original_url: str
    email: str | None = None

class CheckoutIn(BaseModel):
    owner_token: str
    email: str | None = None

# ==== health/debug ====
@app.get("/health")
def health():
    env_ok = {
        "STRIPE_API_KEY": bool(STRIPE_API_KEY),
        "STRIPE_PRICE_ID": bool(STRIPE_PRICE_ID),
        "STRIPE_WEBHOOK_SECRET": bool(STRIPE_WEBHOOK_SECRET),
        "PUBLIC_BASE_URL": bool(PUBLIC_BASE_URL),
    }
    return {"ok": True, "db": DB_PATH, "env": env_ok}

@app.get("/api/admin/state")
def admin_state(owner_token: str = "", email: str = ""):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT plan, email, owner_token FROM users WHERE owner_token=? OR email=? LIMIT 1",
                (owner_token, email))
    user = dict(cur.fetchone()) if cur.fetchone else None
    cur.execute("SELECT COUNT(*) c FROM links WHERE owner_token=?", (owner_token,))
    cnt = cur.fetchone()["c"] if owner_token else None
    conn.close()
    return {"user": user, "links_for_owner": cnt}

# ==== core ====
@app.post("/api/links")
def create_link(payload: CreateLinkIn):
    owner = payload.owner_token.strip()
    url = payload.original_url.strip()
    email = (payload.email or "").strip().lower()
    if not owner or not url:
        raise HTTPException(status_code=400, detail="owner_token and original_url required")

    conn = get_db(); cur = conn.cursor(); n = now_iso()

    # upsert user
    if email:
        cur.execute("SELECT id FROM users WHERE email=? OR owner_token=?", (email, owner))
        r = cur.fetchone()
        if r is None:
            cur.execute("INSERT INTO users (email, owner_token, plan, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (email, owner, 'free', n, n))
        else:
            cur.execute("UPDATE users SET email=?, owner_token=?, updated_at=? WHERE id=?",
                        (email, owner, n, r["id"]))
        conn.commit()

    # effective plan
    cur.execute("SELECT plan FROM users WHERE owner_token=? OR email=?", (owner, email))
    row = cur.fetchone()
    plan = row["plan"] if row else "free"

    if plan != "pro":
        cur.execute("SELECT COUNT(*) c FROM links WHERE owner_token=?", (owner,))
        c = cur.fetchone()["c"]
        if c >= MAX_FREE_LINKS:
            raise HTTPException(status_code=402, detail="Free tier limit reached. Upgrade to Pro.")

    short_code = hex(abs(hash(f"{owner}:{url}:{n}")))[2:10]
    cur.execute("INSERT INTO links (owner_token, original_url, short_code, created_at, plan) VALUES (?,?,?,?,?)",
                (owner, url, short_code, n, plan))
    conn.commit(); conn.close()
    return {"short_code": short_code, "plan": plan}

@app.get("/r/{short_code}")
def redirect(short_code: str, request: Request):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, original_url FROM links WHERE short_code=?", (short_code,))
    row = cur.fetchone()
    if not row: raise HTTPException(status_code=404, detail="Link not found")

    ip = get_ip(request); ua_str, device = get_device_info(request)
    cur.execute("INSERT INTO clicks (link_id, ts, ip, user_agent, device_type) VALUES (?,?,?,?,?)",
                (row["id"], now_iso(), ip, ua_str, device))
    conn.commit(); conn.close()

    return RedirectResponse(url=row["original_url"], status_code=302)

# ==== Stripe (safe mode) ====
@app.post("/api/stripe/checkout")
def checkout(payload: CheckoutIn):
    if not (stripe and STRIPE_API_KEY and STRIPE_PRICE_ID and PUBLIC_BASE_URL):
        # Safe mode: pretend success URL; tell frontend to show message
        return {"checkout_url": f"{PUBLIC_BASE_URL or 'https://example.com'}/stripe-not-configured"}
    session = stripe.checkout.Session.create(
        mode="subscription",
        success_url=f"{PUBLIC_BASE_URL}/api/stripe/success",
        cancel_url=f"{PUBLIC_BASE_URL}/api/stripe/cancel",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        metadata={"owner_token": payload.owner_token, "email": (payload.email or "")},
        customer_email=payload.email if payload.email else None,
        allow_promotion_codes=True
    )
    return {"checkout_url": session.url}

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="stripe-signature")):
    if not (stripe and STRIPE_WEBHOOK_SECRET):
        raise HTTPException(status_code=501, detail="Webhook not configured")
    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(body, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")
    if event["type"] == "checkout.session.completed":
        data = event["data"]["object"]
        md = data.get("metadata", {}) or {}
        owner_token = md.get("owner_token")
        email = md.get("email") or data.get("customer_details", {}).get("email")
        upgrade_user(owner_token, email)
    return JSONResponse({"received": True})
