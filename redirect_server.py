# redirect_server.py — SmartLinks Backend (visual-matched PDF)

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
                                Table, TableStyle)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

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
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return ts
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
    u = (ua or "").lower()
    if "iphone" in u or "android" in u or "mobile" in u:
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
    tablet = 0  # not collected yet
    first_ts = rows[0][0] if rows else None
    last_ts  = rows[-1][0] if rows else None
    return {
        "total": total,
        "unique_visitors": unique_ips,
        "mobile": mobile,
        "desktop": desktop,
        "tablet": tablet,
        "days": days,
        "day_counts": counts,
        "first_pretty": to_pacific_str(first_ts) if first_ts else "-",
        "last_pretty": to_pacific_str(last_ts) if last_ts else "-"
    }

# ----- Style tokens (match V0 look) -----
PURPLE = colors.HexColor("#7C3AED")      # primary
PURPLE_SOFT = colors.HexColor("#EEE7FF") # light panel tint
SLATE_BG = colors.HexColor("#F6F7FB")
BORDER = colors.HexColor("#E5E7EB")
TEXT = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")

# ----- PDF report -----
@app.get("/api/report/{short_code}")
def report_pdf(short_code: str, request: Request):
    # fetch link
    c.execute("SELECT original_url, created_at FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown short code")
    dest, created_at = row
    stats = stats_bundle(short_code)

    # charts dir
    tmpdir = tempfile.mkdtemp()
    activity_path = os.path.join(tmpdir, "daily.png")

    # Daily Activity chart (Mon..Sun) with nicer empty state
    if sum(stats["day_counts"]) == 0:
        # create a soft empty panel
        fig = plt.figure(figsize=(6.2, 2.1))
        ax = fig.add_subplot(111)
        ax.axis("off")
        ax.text(0.5, 0.5, "No activity yet", ha="center", va="center", fontsize=12, color="#9CA3AF")
        fig.tight_layout()
        fig.savefig(activity_path, dpi=200, transparent=True)
        plt.close(fig)
    else:
        ymax = max(stats["day_counts"])
        fig = plt.figure(figsize=(6.2, 2.1))
        ax = fig.add_subplot(111)
        ax.bar(stats["days"], stats["day_counts"])
        ax.set_title("Daily Activity")
        ax.set_ylim(0, ymax * 1.25 if ymax > 0 else 1)
        fig.tight_layout()
        fig.savefig(activity_path, dpi=200)
        plt.close(fig)

    # derived metrics
    total = stats["total"]
    unique_visitors = stats["unique_visitors"]
    scans = total  # in MVP, redirects == scans
    mobile, desktop, tablet = stats["mobile"], stats["desktop"], stats["tablet"]
    def pct(n): 
        return int(round(100 * n / max(1, total)))

    # insights
    peak_idx = max(range(7), key=lambda i: stats["day_counts"][i]) if sum(stats["day_counts"])>0 else None
    peak_day = stats["days"][peak_idx] if peak_idx is not None else "—"
    tip = "Share QR codes during open houses and on social—weekend traffic tends to peak." if peak_day in ["Sat","Sun"] else "Promote your QR on flyers and listing descriptions to boost weekday traffic."

    # PDF doc
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    h_white = ParagraphStyle('h_white', parent=styles['Normal'], textColor=colors.white, fontSize=10)
    title_white = ParagraphStyle('title_white', parent=styles['Title'], textColor=colors.white, fontSize=16, alignment=1)
    small_muted = ParagraphStyle('small_muted', fontSize=9, textColor=MUTED)
    label_muted = ParagraphStyle('label_muted', fontSize=9, textColor=MUTED, alignment=1)
    value_dark = ParagraphStyle('value_dark', fontSize=18, textColor=TEXT, alignment=1)
    heading = ParagraphStyle('heading', fontSize=12, textColor=TEXT, spaceAfter=6)
    normal = ParagraphStyle('normal', fontSize=10, textColor=TEXT)

    story = []

    # Header card (purple)
    header = Table(
        [[Paragraph(f"Property: <u>{dest}</u>", h_white),
          Paragraph(f"Generated: {to_pacific_str(now_local_iso())}", h_white)]],
        colWidths=[doc.width/2-8, doc.width/2-8]
    )
    header.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), PURPLE),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.white),
        ("LEFTPADDING",(0,0),(-1,-1), 12),
        ("RIGHTPADDING",(0,0),(-1,-1), 12),
        ("TOPPADDING",(0,0),(-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
        ("ROUNDEDCORNERS",(0,0),(-1,-1), 8),
    ]))
    story.append(header)
    story.append(Spacer(1, 10))

    # Metric cards row (white cards on light bg)
    def metric_card(title, value):
        t = Table(
            [[Paragraph(f"<b>{value}</b>", value_dark)],
             [Paragraph(title, label_muted)]],
            colWidths=[(doc.width/3)-12]
        )
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1), colors.white),
            ("BOX",(0,0),(-1,-1), 0.6, BORDER),
            ("LEFTPADDING",(0,0),(-1,-1), 12),
            ("RIGHTPADDING",(0,0),(-1,-1), 12),
            ("TOPPADDING",(0,0),(-1,-1), 8),
            ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ]))
        return t

    metrics_row = Table(
        [[metric_card("Total Views", total),
          metric_card("QR Code Scans", scans),
          metric_card("Unique Visitors", unique_visitors)]],
        colWidths=[doc.width/3-8, doc.width/3-8, doc.width/3-8]
    )
    metrics_row.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4)
    ]))
    story.append(metrics_row)
    story.append(Spacer(1, 12))

    # Daily Activity panel (soft gray background)
    activity_panel = Table(
        [[Paragraph("Daily Activity", heading)],
         [Image(activity_path, width=doc.width-16, height=140)]],
        colWidths=[doc.width-16]
    )
    activity_panel.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), SLATE_BG),
        ("BOX",(0,0),(-1,-1), 0.6, BORDER),
        ("LEFTPADDING",(0,0),(-1,-1), 12),
        ("RIGHTPADDING",(0,0),(-1,-1), 12),
        ("TOPPADDING",(0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
    ]))
    story.append(activity_panel)
    story.append(Spacer(1, 12))

    # Device Breakdown with right-aligned percent bars
    story.append(Paragraph("Device Breakdown", heading))

    def percent_row(name, pct):
        bar_total = int((doc.width - 220))  # space for labels + %
        filled = int(bar_total * (pct/100.0))
        bar_tbl = Table([["", ""]], colWidths=[filled, max(0, bar_total-filled)], rowHeights=[8])
        bar_tbl.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0), PURPLE),
            ("BACKGROUND",(1,0),(1,0), colors.HexColor("#E5E7EB")),
            ("BOX",(0,0),(-1,-1), 0.25, colors.HexColor("#D1D5DB")),
        ]))
        row = Table([[Paragraph(name, normal),
                      bar_tbl,
                      Paragraph(f"{pct}%", ParagraphStyle('pct', fontSize=10, textColor=TEXT, alignment=2))]],
                    colWidths=[120, bar_total, 60])
        row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
        return row

    story.append(percent_row("Mobile", pct(mobile)))
    story.append(Spacer(1, 6))
    story.append(percent_row("Desktop", pct(desktop)))
    story.append(Spacer(1, 6))
    story.append(percent_row("Tablet", pct(tablet)))
    story.append(Spacer(1, 12))

    # AI Insights box
    insights_lines = [
        f"Peak engagement: <b>{peak_day}</b>",
        f"Mobile vs Desktop: <b>{pct(mobile)}%</b> / <b>{pct(desktop)}%</b>",
        f"First activity: <b>{stats['first_pretty']}</b> — Last: <b>{stats['last_pretty']}</b>",
        f"Recommended: {tip}",
    ]
    insights_tbl = Table(
        [[Paragraph("🧠 AI Insights", ParagraphStyle('h2', fontSize=12, textColor=PURPLE))]] +
        [[Paragraph(f"• {line}", normal)] for line in insights_lines],
        colWidths=[doc.width]
    )
    insights_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), PURPLE_SOFT),
        ("BOX",(0,0),(-1,-1), 0.6, BORDER),
        ("LEFTPADDING",(0,0),(-1,-1), 12),
        ("RIGHTPADDING",(0,0),(-1,-1), 12),
        ("TOPPADDING",(0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
    ]))
    story.append(insights_tbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph("<i>Powered by SmartLinks — Turning clicks into clients</i>",
                           ParagraphStyle("foot", fontSize=9, textColor=MUTED, alignment=1)))

    doc.build(story)
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

