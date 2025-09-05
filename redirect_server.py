# SmartLinks backend (minimal, production-safe MVP)
# FastAPI + SQLite + device logging + free cap + CSV + QR + PDF
# Start on Render: uvicorn redirect_server:app --host 0.0.0.0 --port $PORT

import os, io, csv, sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

# Third-party libs
import qrcode
from PIL import Image  # noqa: F401 (reportlab needs PIL installed; we import to ensure availability)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors

# user-agents for device detection (with safe fallback)
try:
    from user_agents import parse as parse_ua
except Exception:  # shouldn't happen if requirements installed, but keep fallback
    def parse_ua(_):
        class U:
            is_tablet = False
            is_mobile = False
            is_pc = True
            is_bot = False
        return U()

# ---------------- Config ----------------
DB_PATH = os.getenv("DB_PATH", "smartlinks.db")
MAX_FREE_LINKS = int(os.getenv("MAX_FREE_LINKS", "3"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")  # optional, else derived from request

# ---------------- DB helpers ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db(); cur = conn.cursor()
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
    conn.commit(); conn.close()

# ---------------- App ----------------
app = FastAPI(title="SmartLinks Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
init_db()

# ---------------- Utils ----------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def get_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff: return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri: return xri.strip()
    return request.client.host if request.client else "unknown"

def parse_device(request: Request) -> Tuple[str, str]:
    ua_str = (request.headers.get("user-agent") or "")[:512]
    ua = parse_ua(ua_str)
    if getattr(ua, "is_tablet", False): kind = "tablet"
    elif getattr(ua, "is_mobile", False): kind = "mobile"
    elif getattr(ua, "is_bot", False): kind = "bot"
    elif getattr(ua, "is_pc", False): kind = "desktop"
    else: kind = "unknown"
    return ua_str, kind

def absolute_short_url(request: Request, short_code: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/r/{short_code}"
    base = str(request.base_url).rstrip("/")
    return f"{base}/r/{short_code}"

def owner_plan(cur: sqlite3.Cursor, owner_token: str, email: str) -> str:
    cur.execute("SELECT plan FROM users WHERE owner_token=? OR email=?", (owner_token, email))
    r = cur.fetchone()
    return (r["plan"] if r else "free") or "free"

# ---------------- Schemas ----------------
class CreateLinkIn(BaseModel):
    owner_token: str
    original_url: str
    email: Optional[str] = None

# ---------------- Health ----------------
@app.get("/health")
def health(request: Request):
    return {
        "ok": True,
        "db": DB_PATH,
        "env": {"PUBLIC_BASE_URL": bool(PUBLIC_BASE_URL), "MAX_FREE_LINKS": MAX_FREE_LINKS},
        "example_redirect": absolute_short_url(request, "DEMO1234")
    }

# ---------------- Core ----------------
@app.post("/api/links")
def create_link(payload: CreateLinkIn):
    owner = (payload.owner_token or "").strip()
    url = (payload.original_url or "").strip()
    email = (payload.email or "").strip().lower()
    if not owner or not url:
        raise HTTPException(status_code=400, detail="owner_token and original_url required")

    conn = get_db(); cur = conn.cursor(); n = now_iso()

    # upsert user (bind email to owner)
    if email:
        cur.execute("SELECT id FROM users WHERE email=? OR owner_token=?", (email, owner))
        r = cur.fetchone()
        if r is None:
            cur.execute("INSERT INTO users (email, owner_token, plan, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (email, owner, "free", n, n))
        else:
            cur.execute("UPDATE users SET email=?, owner_token=?, updated_at=? WHERE id=?",
                        (email, owner, n, r["id"]))
        conn.commit()

    plan = owner_plan(cur, owner, email)

    if plan != "pro":
        cur.execute("SELECT COUNT(*) c FROM links WHERE owner_token=?", (owner,))
        c = cur.fetchone()["c"]
        if c >= MAX_FREE_LINKS:
            raise HTTPException(status_code=402, detail="Free tier limit reached. Upgrade to Pro.")

    short_code = hex(abs(hash(f"{owner}:{url}:{n}")))[2:10]
    cur.execute("INSERT INTO links (owner_token, original_url, short_code, created_at, plan) VALUES (?,?,?,?,?)",
                (owner, url, short_code, n, plan))
    conn.commit()

    cur.execute("SELECT id FROM links WHERE short_code=?", (short_code,))
    link_id = cur.fetchone()["id"]
    conn.close()

    return {"id": link_id, "short_code": short_code, "plan": plan}

@app.get("/api/links")
def list_links(owner_token: str = Query(..., description="Owner token")):
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT l.id, l.original_url, l.short_code, l.created_at, l.plan,
               (SELECT COUNT(*) FROM clicks c WHERE c.link_id=l.id) AS clicks
        FROM links l
        WHERE l.owner_token=?
        ORDER BY l.id DESC
    """, (owner_token,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"links": rows}

@app.get("/r/{short_code}")
def redirect(short_code: str, request: Request):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, original_url FROM links WHERE short_code=?", (short_code,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link not found")

    ua_str, device = parse_device(request)
    cur.execute("INSERT INTO clicks (link_id, ts, ip, user_agent, device_type) VALUES (?,?,?,?,?)",
                (row["id"], now_iso(), get_ip(request), ua_str, device))
    conn.commit(); conn.close()

    return RedirectResponse(url=row["original_url"], status_code=302)

# ---------------- Exports ----------------
@app.get("/api/links/{link_id}/clicks.csv")
def export_csv(link_id: int):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id FROM links WHERE id=?", (link_id,))
    if not cur.fetchone(): raise HTTPException(status_code=404, detail="Link not found")

    cur.execute("SELECT ts, ip, user_agent, device_type FROM clicks WHERE link_id=? ORDER BY ts ASC", (link_id,))
    rows = cur.fetchall(); conn.close()

    def gen():
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["timestamp_utc", "ip", "user_agent", "device_type"])
        for r in rows:
            w.writerow([r["ts"], r["ip"], r["user_agent"], r["device_type"]])
        yield out.getvalue()

    return StreamingResponse(gen(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clicks.csv"})

# ---------------- QR ----------------
@app.get("/api/links/{link_id}/qrcode.png")
def qr_png(link_id: int, request: Request, box_size: int = 8, border: int = 2):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT short_code FROM links WHERE id=?", (link_id,))
    row = cur.fetchone(); conn.close()
    if not row: raise HTTPException(status_code=404, detail="Link not found")

    url = absolute_short_url(request, row["short_code"])
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# ---------------- PDF ----------------
@app.get("/api/links/{link_id}/report.pdf")
def report_pdf(link_id: int, request: Request):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT original_url, short_code, created_at FROM links WHERE id=?", (link_id,))
    link = cur.fetchone()
    if not link: raise HTTPException(status_code=404, detail="Link not found")

    cur.execute("SELECT device_type, COUNT(*) c FROM clicks WHERE link_id=? GROUP BY device_type", (link_id,))
    dev_counts = { (r["device_type"] or "unknown"): r["c"] for r in cur.fetchall() }

    cur.execute("SELECT substr(ts,1,10) day, COUNT(*) c FROM clicks WHERE link_id=? GROUP BY day ORDER BY day ASC", (link_id,))
    daily = [ (r["day"], r["c"]) for r in cur.fetchall() ]
    conn.close()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter

    # Header
    c.setFont("Helvetica-Bold", 18); c.drawString(1*inch, H-1*inch, "Seller Report")
    c.setFont("Helvetica", 11)
    c.drawString(1*inch, H-1.3*inch, f"Property URL: {link['original_url']}")
    c.drawString(1*inch, H-1.55*inch, f"SmartLink: {absolute_short_url(request, link['short_code'])}")
    c.drawString(1*inch, H-1.8*inch, f"Created: {link['created_at']} (UTC)")

    # Device bars
    y = H-2.4*inch
    c.setFont("Helvetica-Bold", 12); c.drawString(1*inch, y, "Device Split"); y -= 0.25*inch
    order = ["desktop", "mobile", "tablet", "bot", "unknown"]
    maxv = max([dev_counts.get(k,0) for k in order] + [1])
    for k in order:
        v = dev_counts.get(k, 0)
        c.setFont("Helvetica", 11); c.drawString(1*inch, y, f"{k.capitalize():8} {v}")
        bar_w = 4.5*inch * (v/maxv)
        c.setFillColor(colors.HexColor("#2F80ED"))
        c.rect(2.2*inch, y-0.08*inch, bar_w, 0.18*inch, fill=1, stroke=0)
        c.setFillColor(colors.black)
        y -= 0.35*inch

    # Daily table
    y -= 0.1*inch
    c.setFont("Helvetica-Bold", 12); c.drawString(1*inch, y, "Daily Clicks"); y -= 0.25*inch
    c.setFont("Helvetica", 10)
    if daily:
        for day, cnt in daily:
            c.drawString(1*inch, y, day); c.drawString(3*inch, y, str(cnt)); y -= 0.22*inch
            if y < 1*inch: c.showPage(); y = H-1*inch
    else:
        c.drawString(1*inch, y, "No clicks yet.")

    c.showPage(); c.save(); buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=report.pdf"})
