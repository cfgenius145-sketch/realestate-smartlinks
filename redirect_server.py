# redirect_server.py
import os, hashlib, hmac, sqlite3, json, datetime as dt
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import stripe

# ---------- ENV ----------
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")  # recurring $29/mo price id
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_APP_DOMAIN = os.getenv("PUBLIC_APP_DOMAIN", "http://localhost:8501")  # for success/cancel urls

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

DB_PATH = os.getenv("DB_PATH", "smartlinks.sqlite3")

# ---------- APP ----------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ---------- DB ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # existing tables (simplified—keep your original schemas if richer)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT,
        original_url TEXT NOT NULL,
        slug TEXT UNIQUE,
        created_at TEXT
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link_id INTEGER,
        ts TEXT,
        ip TEXT,
        ua TEXT,
        device TEXT
    );
    """)
    # new owners table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS owners (
        owner_id TEXT PRIMARY KEY,
        email TEXT UNIQUE,
        plan TEXT DEFAULT 'free',
        stripe_customer_id TEXT,
        created_at TEXT
    );
    """)
    # backfill owner_id if column exists but empty (optional)
    # (skip—only needed if migrating from owner_token/session implementation)
    conn.commit()
    conn.close()

init_db()

# ---------- MODELS ----------
class RegisterBody(BaseModel):
    email: EmailStr

class CheckoutBody(BaseModel):
    owner_id: str

# ---------- OWNERS ----------
def email_to_owner_id(email: str) -> str:
    # deterministic stable id; keep simple (salt optional)
    norm = email.strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:24]

def upsert_owner(email: str) -> str:
    oid = email_to_owner_id(email)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT owner_id FROM owners WHERE owner_id=?", (oid,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO owners(owner_id, email, plan, created_at) VALUES(?,?, 'free', ?)",
                    (oid, email.strip().lower(), dt.datetime.utcnow().isoformat()))
        conn.commit()
    conn.close()
    return oid

def get_plan(owner_id: str) -> str:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plan FROM owners WHERE owner_id=?", (owner_id,))
    row = cur.fetchone()
    conn.close()
    return row["plan"] if row else "free"

def set_plan(owner_id: str, plan: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE owners SET plan=? WHERE owner_id=?", (plan, owner_id))
    conn.commit()
    conn.close()

def set_customer(owner_id: str, customer_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE owners SET stripe_customer_id=? WHERE owner_id=?", (customer_id, owner_id))
    conn.commit()
    conn.close()

def link_count(owner_id: str) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM links WHERE owner_id=?", (owner_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return int(c)

def can_create_link(owner_id: str) -> bool:
    plan = get_plan(owner_id)
    if plan == "pro":
        return True
    return link_count(owner_id) < 3

# ---------- API: Accounts ----------
@app.post("/api/owner/register")
async def owner_register(body: RegisterBody):
    oid = upsert_owner(body.email)
    return {"owner_id": oid, "plan": get_plan(oid)}

@app.get("/api/plan/status")
async def plan_status(owner_id: str):
    return {"owner_id": owner_id, "plan": get_plan(owner_id)}

# ---------- API: Stripe Checkout ----------
@app.post("/api/stripe/create-checkout-session")
async def create_checkout_session(body: CheckoutBody):
    if not STRIPE_API_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(500, "Stripe not configured")
    # create a customer if not exists (idempotent via metadata)
    # we don’t have customer email here (optional), but metadata carries owner_id
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{PUBLIC_APP_DOMAIN}?upgrade=success",
            cancel_url=f"{PUBLIC_APP_DOMAIN}?upgrade=cancel",
            metadata={"owner_id": body.owner_id},
            automatic_tax={"enabled": True},
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")

# ---------- Stripe Webhook ----------
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "Webhook secret not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(400, f"Invalid payload: {e}")

    etype = event["type"]

    # Handle Checkout completion → set plan=pro
    if etype == "checkout.session.completed":
        session = event["data"]["object"]
        owner_id = (session.get("metadata") or {}).get("owner_id")
        customer_id = session.get("customer")
        if owner_id:
            set_plan(owner_id, "pro")
            if customer_id:
                set_customer(owner_id, customer_id)

    # Optional: keep pro on subscription events
    if etype in ("invoice.payment_succeeded", "customer.subscription.created", "customer.subscription.updated"):
        obj = event["data"]["object"]
        customer_id = obj.get("customer")
        if customer_id:
            # find owner by customer and ensure plan
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT owner_id FROM owners WHERE stripe_customer_id=?", (customer_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                set_plan(row["owner_id"], "pro")

    # Downgrade if subscription canceled
    if etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        obj = event["data"]["object"]
        customer_id = obj.get("customer")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT owner_id FROM owners WHERE stripe_customer_id=?", (customer_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            set_plan(row["owner_id"], "free")

    return {"received": True}

# ---------- YOUR EXISTING ENDPOINTS ----------
# Example: create a short link (respect plan)
class CreateLinkBody(BaseModel):
    owner_id: str
    url: str
    slug: Optional[str] = None

@app.post("/api/links/create")
async def create_link(body: CreateLinkBody):
    if not can_create_link(body.owner_id):
        raise HTTPException(403, "Free plan limit reached. Upgrade to Pro for unlimited SmartLinks.")
    slug = body.slug or hashlib.md5((body.url + body.owner_id + str(dt.datetime.utcnow())).encode()).hexdigest()[:7]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO links(owner_id, original_url, slug, created_at) VALUES(?,?,?,?)",
                (body.owner_id, body.url, slug, dt.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return {"slug": slug, "short_url": f"/{slug}"}

# Example: redirect handler (keep your current device/ip logging)
@app.get("/{slug}")
async def redirect_slug(slug: str):
    # look up, log click, return redirect response (left as your original)
    from fastapi.responses import RedirectResponse
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, original_url FROM links WHERE slug=?", (slug,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    # log click (simplified)
    cur.execute("INSERT INTO clicks(link_id, ts, ip, ua, device) VALUES(?,?,?,?,?)",
                (row["id"], dt.datetime.utcnow().isoformat(), "", "", ""))
    conn.commit()
    conn.close()
    return RedirectResponse(url=row["original_url"])
