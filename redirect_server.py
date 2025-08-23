# redirect_server.py — known-good minimal backend

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

import sqlite3, os, random, string, io, tempfile

# Headless plotting for charts in PDFs
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

app = FastAPI(title="SmartLinks Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    created = datetime.now().isoformat()
    c.execute(
        "INSERT INTO links (original_url, short_code, created_at, owner_ip) VALUES (?,?,?,?)",
        (data.original_url, code, created, ip)
    )
    conn.commit()

    base = str(request.base_url).rstrip("/")  # use actual public URL
    return {
        "original_url": data.original_url,
        "short_code": code,
        "short_url": f"{base}/{code}",
        "created_at": created,
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
    ts = datetime.now().isoformat()

    c.execute(
        "INSERT INTO clicks (short_code, ts, ip, user_agent, device, city, country) VALUES (?,?,?,?,?,?,?)",
        (short_code, ts, ip, ua, dev, None, None)
    )
    conn.commit()
    return RedirectResponse(url=row[0])

# ----- Helpers for report -----
def _clicks_by_day(short_code):
    rows = c.execute("SELECT ts FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    counts = [0]*7
    for (ts,) in rows:
        try:
            dt = datetime.fromisoformat(ts)
            counts[dt.weekday()] += 1
        except:
            pass
    return days, counts

def _device_split(short_code):
    rows = c.execute("SELECT device, COUNT(*) FROM clicks WHERE short_code=? GROUP BY device", (short_code,)).fetchall()
    d = dict(rows)
    return int(d.get("mobile",0)), int(d.get("desktop",0))

# ----- PDF report (with bar + pie) -----
@app.get("/api/report/{short_code}")
def report_pdf(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown short code")
    dest = row[0]

    c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM clicks WHERE short_code=?", (short_code,))
    total, first_ts, last_ts = c.fetchone() or (0, None, None)
    mobile, desktop = _device_split(short_code)
    days, counts = _clicks_by_day(short_code)

    tmpdir = tempfile.mkdtemp()
    bar_path = os.path.join(tmpdir, "views_by_day.png")
    pie_path = os.path.join(tmpdir, "device_split.png")

    plt.figure(figsize=(4,2.2))
    plt.bar(days, counts)
    plt.title("Views by Day")
    plt.tight_layout()
    plt.savefig(bar_path)
    plt.close()

    plt.figure(figsize=(3,3))
    vals = [mobile, desktop]
    labels = ["Mobile","Desktop"]
    if sum(vals) == 0:
        vals = [1, 0]
    plt.pie(vals, labels=labels, autopct="%1.0f%%", startangle=140)
    plt.title("Device Split")
    plt.tight_layout()
    plt.savefig(pie_path)
    plt.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("🏡 SmartLinks AI Seller Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Listing:</b> {dest}<br/><b>Short Code:</b> {short_code}", styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"<b>Total Views:</b> {total} &nbsp;&nbsp; <b>First:</b> {first_ts or '-'} &nbsp;&nbsp; <b>Last:</b> {last_ts or '-'}",
        styles["Normal"]
    ))
    story.append(Spacer(1, 10))

    tbl = Table([["Metric","Value"],["Mobile Views",str(mobile)],["Desktop Views",str(desktop)]], colWidths=[200,200])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.lightgrey),
        ("GRID",(0,0),(-1,-1),0.5, colors.grey),
        ("FONTNAME",(0,0), (-1,0), "Helvetica-Bold")
    ]))
    story.append(tbl); story.append(Spacer(1, 10))
    story.append(Image(bar_path, width=260, height=150)); story.append(Spacer(1, 8))
    story.append(Image(pie_path, width=200, height=200)); story.append(Spacer(1, 10))
    story.append(Paragraph("<i>Powered by SmartLinks — Turning Clicks into Clients</i>", ParagraphStyle("f", alignment=1, fontSize=10)))
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
