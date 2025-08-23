# redirect_server.py  — full drop-in backend

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import hashlib, sqlite3, os, time, io

# ----- Config -----
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
FREE_LIMIT_PER_IP = int(os.environ.get("FREE_LIMIT_PER_IP", "3"))

# ----- App -----
app = FastAPI(title="SmartLinks Redirect & Analytics")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later to your Streamlit domain
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
)
""")

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
)
""")
conn.commit()

# ----- Helpers -----
def make_code(seed: str) -> str:
    while True:
        code = hashlib.md5((seed + str(time.time())).encode()).hexdigest()[:6]
        c.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
        if not c.fetchone():
            return code

def device_from_ua(ua: str) -> str:
    ua = (ua or "").lower()
    if "iphone" in ua or "android" in ua or "mobile" in ua:
        return "mobile"
    return "desktop"

# ----- APIs -----
class CreateLinkIn(BaseModel):
    original_url: str

@app.post("/api/links")
async def create_link(data: CreateLinkIn, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host)
    # free-plan guard per IP
    c.execute("SELECT COUNT(*) FROM links WHERE owner_ip = ?", (ip,))
    cnt = c.fetchone()[0]
    if cnt >= FREE_LIMIT_PER_IP:
        raise HTTPException(status_code=402, detail="Free plan limit reached. Please upgrade to Pro.")

    code = make_code(data.original_url)
    created = datetime.now().isoformat()
    c.execute("INSERT INTO links (original_url, short_code, created_at, owner_ip) VALUES (?,?,?,?)",
              (data.original_url, code, created, ip))
    conn.commit()
    return {
        "original_url": data.original_url,
        "short_code": code,
        "short_url": f"{PUBLIC_BASE_URL}/{code}",
        "created_at": created,
        "clicks": 0
    }

@app.get("/api/links")
def list_links():
    rows = c.execute("SELECT original_url, short_code, created_at FROM links ORDER BY id DESC").fetchall()
    out = []
    for orig, code, created in rows:
        c.execute("SELECT COUNT(*) FROM clicks WHERE short_code = ?", (code,))
        clicks = c.fetchone()[0]
        out.append({
            "original_url": orig,
            "short_code": code,
            "short_url": f"{PUBLIC_BASE_URL}/{code}",
            "created_at": created,
            "clicks": clicks
        })
    return out

@app.get("/{short_code}")
async def redirect_short_link(short_code: str, request: Request):
    c.execute("SELECT original_url FROM links WHERE short_code = ?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="SmartLink not found")

    ip = request.headers.get("x-forwarded-for", request.client.host)
    ua = request.headers.get("user-agent", "")
    dev = device_from_ua(ua)
    ts = datetime.now().isoformat()

    # (Optional) add geolocation enrichment later
    c.execute("INSERT INTO clicks (short_code, ts, ip, user_agent, device, city, country) VALUES (?,?,?,?,?,?,?)",
              (short_code, ts, ip, ua, dev, None, None))
    conn.commit()

    return RedirectResponse(url=row[0])

# ---- Simple PDF report ----
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

@app.get("/api/report/{short_code}")
def report_pdf(short_code: str):
    c.execute("SELECT original_url FROM links WHERE short_code = ?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown short code")
    dest = row[0]

    c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM clicks WHERE short_code = ?", (short_code,))
    total, first_ts, last_ts = c.fetchone()

    c.execute("SELECT device, COUNT(*) FROM clicks WHERE short_code = ? GROUP BY device", (short_code,))
    device_counts = dict(c.fetchall() or [])
    mobile = device_counts.get("mobile", 0)
    desktop = device_counts.get("desktop", 0)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("🏡 SmartLinks AI Seller Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Listing Link:</b> {dest}<br/><b>Short Code:</b> {short_code}", styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>Total Views:</b> {total} &nbsp;&nbsp; <b>First:</b> {first_ts or '-'} &nbsp;&nbsp; <b>Last:</b> {last_ts or '-'}", styles["Normal"]))
    story.append(Spacer(1, 10))
    table = Table([
        ["Metric", "Value"],
        ["Mobile Views", str(mobile)],
        ["Desktop Views", str(desktop)],
    ], colWidths=[200, 200])
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.lightgrey),
        ("GRID",(0,0),(-1,-1),0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold")
    ]))
    story.append(table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("<i>Powered by SmartLinks — Turning Clicks into Clients</i>",
                           ParagraphStyle("f", alignment=1, fontSize=10)))
    doc.build(story)

    return Response(content=buf.getvalue(), media_type="application/pdf")

