# redirect_server.py — SmartLinks Backend (polished PDF like the mock)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from datetime import datetime
import pytz
import sqlite3, os, random, string, io, tempfile, collections

# Headless plotting
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ReportLab (PDF)
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                Table, TableStyle, Flowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

app = FastAPI(title="SmartLinks Redirect & Analytics")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Timezone helpers (Sacramento / Pacific) -----
PACIFIC = pytz.timezone("America/Los_Angeles")

def now_local_iso() -> str:
    return datetime.now(PACIFIC).isoformat()

def to_pacific_str(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return ts or "-"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    dt_pacific = dt.astimezone(PACIFIC)
    return dt_pacific.strftime("%b %d, %Y %I:%M %p %Z")

# ----- DB -----
conn = sqlite3.connect("realestate_links.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT,
    short_code TEXT UNIQUE,
    created_at TEXT,
    owner_ip TEXT
)""")
c.execute("""
CREATE TABLE IF NOT EXISTS clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT,
    ts TEXT,
    ip TEXT,
    user_agent TEXT,
    device TEXT,
    city TEXT,
    country TEXT
)""")
conn.commit()

# ----- Short-code generator (5 chars) -----
CODE_LEN = 5
ALPHABET = string.ascii_letters + string.digits

def make_code() -> str:
    while True:
        code = ''.join(random.choice(ALPHABET) for _ in range(CODE_LEN))
        c.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
        if not c.fetchone():
            return code

def device_from_ua(ua: str) -> str:
    ua = (ua or "").lower()
    if "iphone" in ua or "android" in ua or "mobile" in ua:
        return "mobile"
    return "desktop"

# ----- Models & Limits -----
class CreateLinkIn(BaseModel):
    original_url: str

FREE_LIMIT_PER_IP = int(os.getenv("FREE_LIMIT_PER_IP", "3"))

# ----- Health -----
@app.get("/")
def root():
    return {"status": "ok"}

# ----- Create Link -----
@app.post("/api/links")
async def create_link(data: CreateLinkIn, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host)
    c.execute("SELECT COUNT(*) FROM links WHERE owner_ip = ?", (ip,))
    if c.fetchone()[0] >= FREE_LIMIT_PER_IP:
        raise HTTPException(status_code=402, detail="Free plan limit reached. Please upgrade to Pro.")

    code = make_code()
    created = now_local_iso()
    c.execute(
        "INSERT INTO links (original_url, short_code, created_at, owner_ip) VALUES (?,?,?,?)",
        (data.original_url, code, created, ip)
    )
    conn.commit()

    base = str(request.base_url).rstrip("/")
    return {
        "original_url": data.original_url,
        "short_code": code,
        "short_url": f"{base}/{code}",
        "created_at": created,
        "created_pretty": to_pacific_str(created),
        "clicks": 0
    }

# ----- List Links -----
@app.get("/api/links")
def list_links(request: Request):
    out = []
    for (orig, code, created) in c.execute("SELECT original_url, short_code, created_at FROM links ORDER BY id DESC"):
        c.execute("SELECT COUNT(*) FROM clicks WHERE short_code = ?", (code,))
        clicks = c.fetchone()[0]
        base = str(request.base_url).rstrip("/")
        out.append({
            "original_url": orig,
            "short_code": code,
            "short_url": f"{base}/{code}",
            "created_at": created,
            "created_pretty": to_pacific_str(created),
            "clicks": clicks
        })
    return out

# ----- Redirect + log click -----
@app.get("/{short_code}")
async def go(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code = ?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="SmartLink not found")

    ip = request.headers.get("x-forwarded-for", request.client.host)
    ua = request.headers.get("user-agent", "")
    dev = device_from_ua(ua)
    ts = now_local_iso()

    c.execute(
        "INSERT INTO clicks (short_code, ts, ip, user_agent, device, city, country) VALUES (?,?,?,?,?,?,?)",
        (short_code, ts, ip, ua, dev, None, None)
    )
    conn.commit()
    return RedirectResponse(url=row[0])

# ----- Analytics helpers -----
def clicks_for(short_code):
    return c.execute("SELECT ts, ip, device FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()

def stats_bundle(short_code):
    rows = clicks_for(short_code)
    total = len(rows)
    unique_ips = len({ip for (_ts, ip, _d) in rows if ip})
    # daily counts (Mon..Sun)
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    counts = [0]*7
    for ts, _ip, _d in rows:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt)
            dt = dt.astimezone(PACIFIC)
            counts[dt.weekday()] += 1
        except:
            pass
    # device split
    dev_counter = collections.Counter([d or "unknown" for _ts,_ip,d in rows])
    mobile = int(dev_counter.get("mobile", 0))
    desktop = int(dev_counter.get("desktop", 0))
    first_ts = rows[0][0] if rows else None
    last_ts  = rows[-1][0] if rows else None
    return {
        "total": total,
        "unique_visitors": unique_ips,
        "mobile": mobile,
        "desktop": desktop,
        "days": days,
        "day_counts": counts,
        "first_pretty": to_pacific_str(first_ts) if first_ts else "-",
        "last_pretty": to_pacific_str(last_ts) if last_ts else "-"
    }

# ----- Pretty components (for PDF) -----
PURPLE = colors.HexColor("#7C3AED")   # primary
PURPLE_DARK = colors.HexColor("#5B21B6")
SLATE_BG = colors.HexColor("#F7F7FB")
TEXT_DARK = colors.HexColor("#111827")

class Divider(Flowable):
    def __init__(self, color=colors.HexColor("#E5E7EB"), width=460, height=1):
        Flowable.__init__(self)
        self.color = color
        self.w = width
        self.h = height
    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.w, self.h, stroke=0, fill=1)

def metric_card(label, value):
    tbl = Table(
        [[Paragraph(f"<b>{value}</b>", ParagraphStyle('v', fontSize=16, textColor=TEXT_DARK, alignment=1)),
          Paragraph(label, ParagraphStyle('l', fontSize=9, textColor=colors.HexColor("#6B7280"), alignment=1))]],
        colWidths=[150, 150]  # not really used, we style via span
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.white),
        ("BOX",(0,0),(-1,-1), 0.5, colors.HexColor("#E5E7EB")),
        ("INNERGRID",(0,0),(-1,-1), 0, colors.white),
        ("VALIGN",(0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1), 10),
        ("RIGHTPADDING",(0,0),(-1,-1), 10),
        ("TOPPADDING",(0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
    ]))
    return tbl

def horiz_bar(percent, label):
    """Return a table with a label and a horizontal bar representing percent."""
    pct = max(0, min(100, int(round(percent))))
    bar_total_w = 300
    filled_w = int(bar_total_w * (pct/100.0))
    # build a small drawing using a table with 2 colored cells
    bar = Table(
        [["", ""]],
        colWidths=[filled_w, bar_total_w - filled_w],
        rowHeights=[8]
    )
    bar.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0), PURPLE),
        ("BACKGROUND",(1,0),(1,0), colors.HexColor("#E5E7EB")),
        ("BOX",(0,0),(-1,-1), 0.25, colors.HexColor("#D1D5DB")),
    ]))
    row = Table(
        [[Paragraph(label, ParagraphStyle('lbl', fontSize=10, textColor=TEXT_DARK)),
          Paragraph(f"{pct}%", ParagraphStyle('pct', fontSize=10, textColor=TEXT_DARK, alignment=2))]],
        colWidths=[200, 40]
    )
    row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    return row, bar

# ----- PDF report (polished) -----
@app.get("/api/report/{short_code}")
def report_pdf(short_code: str, request: Request):
    # fetch link
    c.execute("SELECT original_url, created_at FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown short code")
    dest, created_at = row
    stats = stats_bundle(short_code)

    # images for charts
    tmpdir = tempfile.mkdtemp()
    activity_path = os.path.join(tmpdir, "daily.png")

    # Daily Activity chart (Mon..Sun)
    plt.figure(figsize=(5.2,2.0))
    plt.bar(stats["days"], stats["day_counts"])
    plt.title("Daily Activity")
    plt.tight_layout()
    plt.savefig(activity_path, dpi=200)
    plt.close()

    # derive insights
    peak_idx = max(range(7), key=lambda i: stats["day_counts"][i]) if sum(stats["day_counts"])>0 else None
    peak_day = stats["days"][peak_idx] if peak_idx is not None else "—"
    total = stats["total"]
    mobile = stats["mobile"]
    desktop = stats["desktop"]
    mob_pct = round(100*mobile/max(1,total))
    desk_pct = round(100*desktop/max(1,total))
    tip = "Share QR codes during open houses and on social—weekend traffic tends to peak." if peak_day in ["Sat","Sun"] else "Promote your QR on flyers and listing descriptions to boost weekday traffic."

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('title', parent=styles['Title'], fontSize=18, textColor=colors.white, alignment=1)
    small_w = ParagraphStyle('smallw', fontSize=9, textColor=colors.white)
    small = ParagraphStyle('small', fontSize=9, textColor=colors.HexColor("#6B7280"))
    heading = ParagraphStyle('heading', fontSize=12, textColor=TEXT_DARK, spaceAfter=6)
    normal = ParagraphStyle('normal', fontSize=10, textColor=TEXT_DARK)

    story = []

    # Header "card"
    # Draw a full-width purple rounded rectangle using a canvas callback
    def header_canv(canv, doc_obj):
        canv.saveState()
        canv.setFillColor(PURPLE)
        x, y, w, h = 36, doc_obj.height + doc_obj.topMargin - 10, doc_obj.width, 60
        canv.roundRect(x, y, w, h, 10, stroke=0, fill=1)
        canv.restoreState()

    story.append(Spacer(1, 8))
    story.append(Paragraph("SmartLinks AI Seller Report", title))
    story.append(Spacer(1, 2))
    # top line: property + generated info (in white, on purple)
    header_tbl = Table([
        [Paragraph(f"Property: <u>{dest}</u>", small_w),
         Paragraph(f"Generated: {to_pacific_str(now_local_iso())}", small_w)]
    ], colWidths=[doc.width/2-6, doc.width/2-6])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), PURPLE),
        ("LEFTPADDING",(0,0),(-1,-1), 12),
        ("RIGHTPADDING",(0,0),(-1,-1), 12),
        ("TOPPADDING",(0,0),(-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 10))

    # Metrics row (cards)
    cards = [
        metric_card("Total Views", stats["total"]),
        metric_card("QR Code Scans", stats["total"]),  # scans == total redirects in this MVP
        metric_card("Unique Visitors", stats["unique_visitors"]),
    ]
    cards_tbl = Table([cards], colWidths=[doc.width/3-8, doc.width/3-8, doc.width/3-8])
    cards_tbl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]))
    story.append(cards_tbl)
    story.append(Spacer(1, 10))

    # Daily Activity chart
    story.append(Paragraph("Daily Activity", heading))
    story.append(Image(activity_path, width=doc.width, height=140))
    story.append(Spacer(1, 12))

    # Device Breakdown with bars
    story.append(Paragraph("Device Breakdown", heading))
    mob_row, mob_bar = horiz_bar(mob_pct, "Mobile")
    desk_row, desk_bar = horiz_bar(desk_pct, "Desktop")
    story.append(mob_row); story.append(Spacer(1, 2)); story.append(mob_bar); story.append(Spacer(1, 8))
    story.append(desk_row); story.append(Spacer(1, 2)); story.append(desk_bar); story.append(Spacer(1, 12))

    # AI Insights box
    insights = [
        f"Peak engagement: <b>{peak_day}</b>",
        f"Mobile vs Desktop: <b>{mob_pct}%</b> / <b>{desk_pct}%</b>",
        f"First activity: <b>{stats['first_pretty']}</b> — Last: <b>{stats['last_pretty']}</b>",
        f"Recommended: {tip}"
    ]
    insights_tbl = Table([[Paragraph("🧠 AI Insights", ParagraphStyle('h2', fontSize=12, textColor=PURPLE_DARK))]] +
                         [[Paragraph(f"• {line}", normal)] for line in insights],
                         colWidths=[doc.width])
    insights_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), SLATE_BG),
        ("BOX",(0,0),(-1,-1), 0.6, colors.HexColor("#E5E7EB")),
        ("LEFTPADDING",(0,0),(-1,-1), 10),
        ("RIGHTPADDING",(0,0),(-1,-1), 10),
        ("TOPPADDING",(0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
    ]))
    story.append(insights_tbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph("<i>Powered by SmartLinks — Turning clicks into clients</i>",
                           ParagraphStyle("foot", fontSize=9, textColor=colors.HexColor("#6B7280"), alignment=1)))
    doc.build(story)  # (header shape is already integrated via the purple tables)

    return Response(content=buf.getvalue(), media_type="application/pdf")

# ----- CSV export -----
@app.get("/api/report/{short_code}/csv")
def report_csv(short_code: str):
    rows = c.execute("SELECT ts, ip, user_agent, device, city, country FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    out = io.StringIO()
    out.write("timestamp,ip,user_agent,device,city,country\n")
    for r in rows:
        out.write(",".join([str(x) if x is not None else "" for x in r]) + "\n")
    return Response(content=out.getvalue(), media_type="text/csv")
