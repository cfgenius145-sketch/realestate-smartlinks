# redirect_server.py — SmartLinks Backend (golden baseline)
# FastAPI + SQLite + PDF report; 3 free links; per-browser token (header) with IP fallback.

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, sqlite3, random, string, io, tempfile, collections, re
from datetime import datetime
import pytz

# headless plotting
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------- config ----------
FREE_LIMIT = int(os.getenv("FREE_LIMIT_PER_IP", "3"))
PACIFIC = pytz.timezone("America/Los_Angeles")
def now_local_iso(): return datetime.now(PACIFIC).isoformat()
def to_pacific_str(ts: Optional[str]) -> str:
    if not ts: return "-"
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return ts
    if dt.tzinfo is None: dt = pytz.utc.localize(dt)
    return dt.astimezone(PACIFIC).strftime("%b %d, %Y %I:%M %p %Z")

# ---------- app ----------
app = FastAPI(title="SmartLinks Redirect & Analytics")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ---------- db ----------
conn = sqlite3.connect("realestate_links.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_url TEXT,
  short_code TEXT UNIQUE,
  created_at TEXT,
  owner_key  TEXT
)""")
c.execute("""CREATE TABLE IF NOT EXISTS clicks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  short_code TEXT,
  ts TEXT,
  ip TEXT,
  user_agent TEXT,
  device TEXT
)""")
conn.commit()

# ---------- utils ----------
CODE_LEN = 5
ALPHABET = string.ascii_letters + string.digits
def make_code()->str:
    while True:
        code = ''.join(random.choice(ALPHABET) for _ in range(CODE_LEN))
        c.execute("SELECT 1 FROM links WHERE short_code=?", (code,))
        if not c.fetchone(): return code

def device_from_ua(ua:str)->str:
    u = (ua or "").lower()
    if any(t in u for t in ["ipad","tablet","kindle","silk/"]): return "tablet"
    if any(t in u for t in ["iphone","android","mobile","ipod","iemobile","opera mini",
                            "fbav","instagram","tiktok","micromessenger","pinterest","line"]): return "mobile"
    return "desktop"

def resolve_owner(request: Request, x_owner_token: Optional[str])->str:
    # Prefer client token (Streamlit sends this). Fallback to first IP.
    tok = (x_owner_token or "").strip()
    if tok: return f"tok:{tok[:64]}"
    xff = request.headers.get("x-forwarded-for","")
    ip = (xff.split(",")[0].strip() if xff else (request.client.host or "unknown"))
    return f"ip:{ip}"

# ---------- models ----------
class CreateLinkIn(BaseModel):
    original_url: str

# ---------- health ----------
@app.get("/")
def health(): return {"status":"ok"}

# ---------- create link ----------
@app.post("/api/links")
def create_link(data: CreateLinkIn, request: Request, x_owner_token: Optional[str]=Header(default=None, alias="X-Owner-Token")):
    owner = resolve_owner(request, x_owner_token)
    # free cap
    c.execute("SELECT COUNT(*) FROM links WHERE owner_key=?", (owner,))
    if (c.fetchone()[0] or 0) >= FREE_LIMIT:
        raise HTTPException(402, "Free plan limit reached (3 SmartLinks).")
    code = make_code()
    created = now_local_iso()
    c.execute("INSERT INTO links(original_url,short_code,created_at,owner_key) VALUES (?,?,?,?)",
              (data.original_url, code, created, owner))
    conn.commit()
    base = str(request.base_url).rstrip("/")
    return {"original_url": data.original_url, "short_code": code,
            "short_url": f"{base}/{code}",
            "created_at": created, "created_pretty": to_pacific_str(created),
            "clicks": 0}

# ---------- list links (owner only) ----------
@app.get("/api/links")
def list_links(request: Request, x_owner_token: Optional[str]=Header(default=None, alias="X-Owner-Token")):
    owner = resolve_owner(request, x_owner_token)
    out = []
    for (orig, code, created) in c.execute(
        "SELECT original_url, short_code, created_at FROM links WHERE owner_key=? ORDER BY id DESC", (owner,)
    ):
        c.execute("SELECT COUNT(*) FROM clicks WHERE short_code=?", (code,))
        clicks = c.fetchone()[0]
        base = str(request.base_url).rstrip("/")
        out.append({"original_url": orig, "short_code": code, "short_url": f"{base}/{code}",
                    "created_at": created, "created_pretty": to_pacific_str(created), "clicks": clicks})
    return out

# ---------- redirect + click ----------
@app.get("/{short_code}")
def go(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row: raise HTTPException(404, "SmartLink not found")
    dest = row[0]
    ua = request.headers.get("user-agent","")
    dev = device_from_ua(ua)
    ip = (request.headers.get("x-forwarded-for","").split(",")[0].strip()
          or (request.client.host or ""))
    c.execute("INSERT INTO clicks(short_code,ts,ip,user_agent,device) VALUES (?,?,?,?,?)",
              (short_code, now_local_iso(), ip, ua, dev))
    conn.commit()
    return RedirectResponse(url=dest)

# ---------- analytics helpers ----------
def stats(short_code):
    rows = c.execute("SELECT ts, device FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    total = len(rows)
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]; counts = [0]*7
    for ts, _ in rows:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None: dt = pytz.utc.localize(dt)
            counts[dt.astimezone(PACIFIC).weekday()] += 1
        except: pass
    dev_counter = collections.Counter([d for _t, d in rows])
    return {"total": total, "days": days, "day_counts": counts,
            "mobile": dev_counter.get("mobile",0), "desktop": dev_counter.get("desktop",0), "tablet": dev_counter.get("tablet",0)}

# ---------- report PDF ----------
@app.get("/api/report/{short_code}")
def report_pdf(short_code: str, request: Request):
    c.execute("SELECT original_url, created_at FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row: raise HTTPException(404, "Unknown short code")
    dest, created = row
    s = stats(short_code)

    # chart
    tmpdir = tempfile.mkdtemp()
    chart_path = os.path.join(tmpdir, "daily.png")
    fig = plt.figure(figsize=(6.2, 2.1)); ax = fig.add_subplot(111)
    if sum(s["day_counts"]) == 0:
        ax.axis("off"); ax.text(0.5,0.5,"No activity yet",ha="center",va="center",fontsize=12,color="#9CA3AF")
    else:
        ax.bar(s["days"], s["day_counts"]); ax.set_title("Daily Activity")
    fig.tight_layout(); fig.savefig(chart_path, dpi=200); plt.close(fig)

    PURPLE = colors.HexColor("#7C3AED"); BORDER = colors.HexColor("#E5E7EB")
    TEXT = colors.HexColor("#111827"); MUTED = colors.HexColor("#6B7280")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    h_white = ParagraphStyle('h_white', parent=styles['Normal'], textColor=colors.white, fontSize=10)
    label = ParagraphStyle('label', fontSize=9, textColor=MUTED, alignment=1)
    val = ParagraphStyle('val', fontSize=18, textColor=TEXT, alignment=1)
    heading = ParagraphStyle('heading', fontSize=12, textColor=TEXT)

    story=[]
    header = Table([[Paragraph(f"Property: <u>{dest}</u>", h_white),
                     Paragraph(f"Generated: {to_pacific_str(now_local_iso())}", h_white)]],
                   colWidths=[doc.width/2-8, doc.width/2-8])
    header.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), PURPLE),
                                ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
                                ("TOPPADDING",(0,0),(-1,-1),10), ("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    story += [header, Spacer(1,10)]

    def card(t,v):
        T = Table([[Paragraph(f"<b>{v}</b>", val)],[Paragraph(t, label)]], colWidths=[(doc.width/3)-12])
        T.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,BORDER),
                               ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
                               ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),8)]))
        return T

    metrics = Table([[card("Total Views", s["total"]), card("Mobile", s["mobile"]), card("Desktop", s["desktop"])]],
                    colWidths=[doc.width/3-8]*3)
    metrics.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                 ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4)]))
    story += [metrics, Spacer(1,12)]

    story += [Paragraph("Daily Activity", heading),
              Image(chart_path, width=doc.width, height=140),
              Spacer(1,8),
              Paragraph("<i>Powered by SmartLinks — Turning clicks into clients</i>",
                        ParagraphStyle("foot", fontSize=9, textColor=MUTED, alignment=1))]

    doc.build(story)
    return Response(content=buf.getvalue(), media_type="application/pdf")

# ---------- CSV ----------
@app.get("/api/report/{short_code}/csv")
def report_csv(short_code: str):
    rows = c.execute("SELECT ts, ip, user_agent, device FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    out = io.StringIO(); out.write("timestamp,ip,user_agent,device\n")
    for r in rows: out.write(",".join([str(x) if x is not None else "" for x in r]) + "\n")
    return Response(content=out.getvalue(), media_type="text/csv")
