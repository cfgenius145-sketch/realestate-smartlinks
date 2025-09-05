# redirect_server.py — SmartLinks Backend (token from header/body/query; Stripe; 3-free cap)
# FastAPI + SQLite + ReportLab + Stripe

from fastapi import FastAPI, HTTPException, Request, Header, Query
from fastapi.responses import RedirectResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

import os, sqlite3, random, string, io, tempfile, collections, re
from datetime import datetime
import pytz

# Plotting headless
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Stripe
import stripe

# ---------- Config ----------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID   = os.getenv("STRIPE_PRICE_ID", "")      # price_XXXX ($29/mo)
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_LIMIT = int(os.getenv("FREE_LIMIT_PER_IP", "3"))  # keep existing env var name

PACIFIC = pytz.timezone("America/Los_Angeles")
def now_local_iso(): return datetime.now(PACIFIC).isoformat()
def to_pacific_str(ts: Optional[str]) -> str:
    if not ts: return "-"
    try: dt = datetime.fromisoformat(ts)
    except Exception: return ts
    if dt.tzinfo is None: dt = pytz.utc.localize(dt)
    return dt.astimezone(PACIFIC).strftime("%b %d, %Y %I:%M %p %Z")

# ---------- App ----------
app = FastAPI(title="SmartLinks Redirect & Analytics")
app.add_middleware(
    CORSMiddleware(
        allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )
)

# ---------- DB ----------
conn = sqlite3.connect("realestate_links.db", check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS owners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_key TEXT UNIQUE,
  plan TEXT DEFAULT 'free',
  created_at TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_url TEXT,
  short_code  TEXT UNIQUE,
  created_at  TEXT,
  owner_key   TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS clicks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  short_code TEXT,
  ts         TEXT,
  ip         TEXT,
  user_agent TEXT,
  device     TEXT,
  city       TEXT,
  country    TEXT
)""")
conn.commit()

# ---------- Helpers ----------
CODE_LEN = 5
ALPHABET = string.ascii_letters + string.digits

def make_code() -> str:
    while True:
        code = ''.join(random.choice(ALPHABET) for _ in range(CODE_LEN))
        c.execute("SELECT 1 FROM links WHERE short_code=?", (code,))
        if not c.fetchone():
            return code

def classify_device(ua: str) -> str:
    u = (ua or "").lower()
    if any(t in u for t in ["ipad","tablet","kindle","silk/"]): return "tablet"
    if any(t in u for t in ["iphone","android","mobile","ipod","iemobile","opera mini",
                            "fbav","instagram","tiktok","micromessenger","pinterest","line"]):
        return "mobile"
    return "desktop"

def ensure_owner(owner_key: str):
    c.execute("SELECT plan FROM owners WHERE owner_key=?", (owner_key,))
    if not c.fetchone():
        c.execute("INSERT INTO owners (owner_key, plan, created_at) VALUES (?,?,?)",
                  (owner_key, 'free', now_local_iso()))
        conn.commit()

def owner_plan(owner_key: str) -> str:
    ensure_owner(owner_key)
    c.execute("SELECT plan FROM owners WHERE owner_key=?", (owner_key,))
    row = c.fetchone()
    return (row[0] if row else 'free')

def set_owner_plan(owner_key: str, plan: str):
    ensure_owner(owner_key)
    c.execute("UPDATE owners SET plan=? WHERE owner_key=?", (plan, owner_key))
    conn.commit()

# ---- Extract owner token from header OR body OR query (robust) ----
def resolve_owner_token(
    x_owner_token_header: Optional[str],
    owner_token_body: Optional[str],
    owner_token_query: Optional[str]
) -> str:
    tok = (x_owner_token_header or owner_token_body or owner_token_query or "").strip()
    if not tok:
        raise HTTPException(400, "Missing owner token")
    return f"tok:{tok[:64]}"

# ---------- Schemas ----------
class CreateLinkIn(BaseModel):
    original_url: str
    owner_token: Optional[str] = None  # NEW: allow token in body

class CheckoutIn(BaseModel):
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None
    owner_token: Optional[str] = None  # allow body token here too

# ---------- Health ----------
@app.get("/")
def health(): return {"status": "ok"}

# ---------- Owner status ----------
@app.get("/api/plan")
def get_plan(
    x_owner_token: Optional[str] = Header(default=None, alias="X-Owner-Token"),
    owner_token: Optional[str] = Query(None)
):
    owner = resolve_owner_token(x_owner_token, None, owner_token)
    return {"plan": owner_plan(owner)}

# ---------- Create link ----------
@app.post("/api/links")
def create_link(
    data: CreateLinkIn,
    request: Request,
    x_owner_token: Optional[str] = Header(default=None, alias="X-Owner-Token"),
):
    owner = resolve_owner_token(x_owner_token, data.owner_token, None)
    ensure_owner(owner)
    if owner_plan(owner) != "pro":
        c.execute("SELECT COUNT(*) FROM links WHERE owner_key=?", (owner,))
        if (c.fetchone()[0] or 0) >= FREE_LIMIT:
            raise HTTPException(402, "Free plan limit reached. Please upgrade to Pro.")

    code = make_code(); created = now_local_iso()
    c.execute("INSERT INTO links (original_url, short_code, created_at, owner_key) VALUES (?,?,?,?)",
              (data.original_url, code, created, owner))
    conn.commit()
    base = str(request.base_url).rstrip("/")
    return {"original_url": data.original_url, "short_code": code,
            "short_url": f"{base}/{code}", "created_at": created,
            "created_pretty": to_pacific_str(created), "clicks": 0}

# ---------- List links (owner scope) ----------
@app.get("/api/links")
def list_links(
    request: Request,
    x_owner_token: Optional[str] = Header(default=None, alias="X-Owner-Token"),
    owner_token: Optional[str] = Query(None)
):
    owner = resolve_owner_token(x_owner_token, None, owner_token)
    ensure_owner(owner)
    out = []
    for (orig, code, created) in c.execute(
        "SELECT original_url, short_code, created_at FROM links WHERE owner_key=? ORDER BY id DESC",
        (owner,)
    ):
        c.execute("SELECT COUNT(*) FROM clicks WHERE short_code=?", (code,))
        clicks = c.fetchone()[0]
        base = str(request.base_url).rstrip("/")
        out.append({"original_url": orig, "short_code": code,
                    "short_url": f"{base}/{code}",
                    "created_at": created, "created_pretty": to_pacific_str(created),
                    "clicks": clicks})
    return out

# ---------- Redirect + click ----------
@app.get("/{short_code}")
def go(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row: raise HTTPException(404, "SmartLink not found")
    dest = row[0]
    ua = request.headers.get("user-agent", "")
    ip_raw = request.headers.get("x-forwarded-for") or (request.client.host or "")
    dev = "desktop"
    try: dev = classify_device(ua)
    except: pass
    c.execute("INSERT INTO clicks (short_code, ts, ip, user_agent, device, city, country) VALUES (?,?,?,?,?,?,?)",
              (short_code, now_local_iso(), ip_raw, ua, dev, None, None))
    conn.commit()
    return RedirectResponse(url=dest)

# ---------- Analytics helpers ----------
def clicks_for(short_code):
    return c.execute("SELECT ts, ip, device FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()

def stats_bundle(short_code):
    rows = clicks_for(short_code)
    total = len(rows)
    unique_ips = len({ip for (_ts, ip, _d) in rows if ip})
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]; counts = [0]*7
    for ts, _ip, _d in rows:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None: dt = pytz.utc.localize(dt)
            counts[dt.astimezone(PACIFIC).weekday()] += 1
        except: pass
    dev_counter = collections.Counter([d or "unknown" for _ts,_ip,d in rows])
    mobile = int(dev_counter.get("mobile", 0))
    desktop = int(dev_counter.get("desktop", 0))
    tablet = int(dev_counter.get("tablet", 0))
    first_ts = rows[0][0] if rows else None; last_ts = rows[-1][0] if rows else None
    return {"total": total, "unique_visitors": unique_ips, "mobile": mobile, "desktop": desktop, "tablet": tablet,
            "days": days, "day_counts": counts, "first_pretty": to_pacific_str(first_ts) if first_ts else "-",
            "last_pretty": to_pacific_str(last_ts) if last_ts else "-"}

# ---------- PDF report ----------
PURPLE = colors.HexColor("#7C3AED"); PURPLE_SOFT = colors.HexColor("#EEE7FF")
SLATE_BG = colors.HexColor("#F6F7FB"); BORDER = colors.HexColor("#E5E7EB")
TEXT = colors.HexColor("#111827"); MUTED = colors.HexColor("#6B7280")

@app.get("/api/report/{short_code}")
def report_pdf(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row: raise HTTPException(404, "Unknown short code")
    dest = row[0]
    stats = stats_bundle(short_code)

    tmpdir = tempfile.mkdtemp(); activity_path = os.path.join(tmpdir, "daily.png")
    if sum(stats["day_counts"]) == 0:
        fig = plt.figure(figsize=(6.2, 2.1)); ax = fig.add_subplot(111); ax.axis("off")
        ax.text(0.5, 0.5, "No activity yet", ha="center", va="center", fontsize=12, color="#9CA3AF")
        fig.tight_layout(); fig.savefig(activity_path, dpi=200, transparent=True); plt.close(fig)
    else:
        ymax = max(stats["day_counts"])
        fig = plt.figure(figsize=(6.2, 2.1)); ax = fig.add_subplot(111)
        ax.bar(stats["days"], stats["day_counts"]); ax.set_title("Daily Activity")
        ax.set_ylim(0, ymax*1.25 if ymax>0 else 1)
        fig.tight_layout(); fig.savefig(activity_path, dpi=200); plt.close(fig)

    total, uniq = stats["total"], stats["unique_visitors"]; scans = total
    mob, desk, tab = stats["mobile"], stats["desktop"], stats["tablet"]
    pct = lambda n: int(round(100*n/max(1,total)))
    peak_idx = max(range(7), key=lambda i: stats["day_counts"][i]) if sum(stats["day_counts"])>0 else None
    peak_day = stats["days"][peak_idx] if peak_idx is not None else "—"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    h_white = ParagraphStyle('h_white', parent=styles['Normal'], textColor=colors.white, fontSize=10)
    label_muted = ParagraphStyle('label_muted', fontSize=9, textColor=MUTED, alignment=1)
    value_dark = ParagraphStyle('value_dark', fontSize=18, textColor=TEXT, alignment=1)
    heading = ParagraphStyle('heading', fontSize=12, textColor=TEXT, spaceAfter=6)
    normal = ParagraphStyle('normal', fontSize=10, textColor=TEXT)

    story = []
    header = Table([[Paragraph(f"Property: <u>{dest}</u>", h_white),
                     Paragraph(f"Generated: {to_pacific_str(now_local_iso())}", h_white)]],
                   colWidths=[doc.width/2-8, doc.width/2-8])
    header.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), PURPLE), ("TEXTCOLOR",(0,0),(-1,-1), colors.white),
        ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),10), ("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("ROUNDEDCORNERS",(0,0),(-1,-1),8),
    ]))
    story.append(header); story.append(Spacer(1,10))

    def card(title, value):
        t = Table([[Paragraph(f"<b>{value}</b>", value_dark)],[Paragraph(title, label_muted)]],
                  colWidths=[(doc.width/3)-12])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1), colors.white), ("BOX",(0,0),(-1,-1), 0.6, BORDER),
            ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
            ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),8),
        ])); return t

    metrics = Table([[card("Total Views", total), card("QR Code Scans", scans), card("Unique Visitors", uniq)]],
                    colWidths=[doc.width/3-8, doc.width/3-8, doc.width/3-8])
    metrics.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                 ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4)]))
    story.append(metrics); story.append(Spacer(1,12))

    panel = Table([[Paragraph("Daily Activity", heading)],
                   [Image(activity_path, width=doc.width-16, height=140)]],
                  colWidths=[doc.width-16])
    panel.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#F6F7FB")),
                               ("BOX",(0,0),(-1,-1), 0.6, colors.HexColor("#E5E7EB")),
                               ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
                               ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    story.append(panel); story.append(Spacer(1,12))

    def percent_row(name, p):
        total_w = int((doc.width - 220)); filled = int(total_w*(p/100.0))
        bar = Table([["",""]], colWidths=[filled, max(0,total_w-filled)], rowHeights=[8])
        bar.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0),colors.HexColor("#7C3AED")),
            ("BACKGROUND",(1,0),(1,0),colors.HexColor("#E5E7EB")),
            ("BOX",(0,0),(-1,-1),0.25,colors.HexColor("#D1D5DB"))
        ]))
        row = Table([[Paragraph(name, normal), bar,
                      Paragraph(f"{p}%", ParagraphStyle('pct', fontSize=10, textColor=colors.HexColor("#111827"), alignment=2))]],
                    colWidths=[120, total_w, 60])
        row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
        return row

    story.append(Paragraph("Device Breakdown", heading))
    story.append(percent_row("Mobile", pct(mob))); story.append(Spacer(1,6))
    story.append(percent_row("Desktop", pct(desk))); story.append(Spacer(1,6))
    story.append(percent_row("Tablet", pct(tab))); story.append(Spacer(1,12))

    insights = Table(
        [[Paragraph("🧠 AI Insights", ParagraphStyle('h2', fontSize=12, textColor=colors.HexColor("#7C3AED")))]]
        + [[Paragraph(f"• Peak engagement: <b>{peak_day}</b>", normal)]],
        colWidths=[doc.width]
    )
    insights.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#EEE7FF")),
        ("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#E5E7EB")),
        ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(insights); story.append(Spacer(1,10))
    story.append(Paragraph("<i>Powered by SmartLinks — Turning clicks into clients</i>",
                           ParagraphStyle("foot", fontSize=9, textColor=colors.HexColor("#6B7280"), alignment=1)))
    doc.build(story)
    return Response(content=buf.getvalue(), media_type="application/pdf")

# ---------- CSV ----------
@app.get("/api/report/{short_code}/csv")
def report_csv(short_code: str):
    rows = c.execute("SELECT ts, ip, user_agent, device, city, country FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    out = io.StringIO(); out.write("timestamp,ip,user_agent,device,city,country\n")
    for r in rows: out.write(",".join([str(x) if x is not None else "" for x in r]) + "\n")
    return Response(content=out.getvalue(), media_type="text/csv")

# ---------- Stripe: create Checkout Session ----------
@app.post("/api/checkout")
def create_checkout(
    request: Request,
    body: CheckoutIn,
    x_owner_token: Optional[str] = Header(default=None, alias="X-Owner-Token")
):
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(500, "Stripe is not configured on the server.")
    owner = resolve_owner_token(x_owner_token, body.owner_token, None)

    base_url = str(request.base_url).rstrip("/")
    success_url = body.success_url or base_url + "/"
    cancel_url  = body.cancel_url  or base_url + "/"

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"owner_key": owner},
        allow_promotion_codes=True
    )
    return {"url": session.url}

# ---------- Stripe webhook ----------
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"status":"ignored (no webhook secret)"}, status_code=200)
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if event["type"] == "checkout.session.completed":
        data = event["data"]["object"]
        owner = (data.get("metadata") or {}).get("owner_key")
        if owner: set_owner_plan(owner, "pro")
    return {"status": "ok"}
