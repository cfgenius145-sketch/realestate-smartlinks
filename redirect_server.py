# --- NEW REPORT ENDPOINTS WITH CHARTS + CSV ---
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import io, matplotlib.pyplot as plt, tempfile, csv
import hashlib, time  # add if missing
import os, random, string

CODE_LEN = 5  # shorten from 6 to 5 safely
ALPHABET = string.ascii_letters + string.digits  # base62

def make_code(seed: str) -> str:
    """Generate a unique base62 short code of length CODE_LEN."""
    while True:
        code = ''.join(random.choice(ALPHABET) for _ in range(CODE_LEN))
        c.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
        if not c.fetchone():
            return code


# --- Short-code generator (5 chars) ---
def make_code(seed: str) -> str:
    while True:
        code = hashlib.md5((seed + str(time.time())).encode()).hexdigest()[:5]  # <- length here
        c.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
        if not c.fetchone():
            return code


def _clicks_by_day(short_code):
    rows = c.execute("SELECT ts FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    # buckets Mon..Sun
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    counts = [0]*7
    for (ts,) in rows:
        try:
            dt = datetime.fromisoformat(ts)
            counts[dt.weekday()] += 1
        except: pass
    return days, counts

def _device_split(short_code):
    rows = c.execute("SELECT device, COUNT(*) FROM clicks WHERE short_code=? GROUP BY device", (short_code,)).fetchall()
    d = dict(rows)
    return int(d.get("mobile",0)), int(d.get("desktop",0))

@app.get("/api/report/{short_code}")
def report_pdf(short_code: str):
    c.execute("SELECT original_url FROM links WHERE short_code=?", (short_code,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown short code")
    dest = row[0]

    c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM clicks WHERE short_code=?", (short_code,))
    total, first_ts, last_ts = c.fetchone()
    mobile, desktop = _device_split(short_code)
    days, counts = _clicks_by_day(short_code)

    # Make charts to temp files
    tmpdir = tempfile.mkdtemp()
    bar_path = os.path.join(tmpdir, "views_by_day.png")
    pie_path = os.path.join(tmpdir, "device_split.png")

    plt.figure(figsize=(4,2.2))
    plt.bar(days, counts); plt.title("Views by Day"); plt.tight_layout(); plt.savefig(bar_path); plt.close()

    plt.figure(figsize=(3,3))
    vals = [mobile, desktop]
    labels = ["Mobile","Desktop"]
    plt.pie(vals if sum(vals)>0 else [1], labels=labels, autopct="%1.0f%%"); plt.title("Device Split"); plt.tight_layout(); plt.savefig(pie_path); plt.close()

    # Build PDF
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("🏡 SmartLinks AI Seller Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Listing:</b> {dest}<br/><b>Short Code:</b> {short_code}", styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>Total Views:</b> {total} &nbsp;&nbsp; <b>First:</b> {first_ts or '-'} &nbsp;&nbsp; <b>Last:</b> {last_ts or '-'}", styles["Normal"]))
    story.append(Spacer(1, 10))

    tbl = Table([["Metric","Value"],["Mobile Views",str(mobile)],["Desktop Views",str(desktop)]], colWidths=[200,200])
    tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.lightgrey),("GRID",(0,0),(-1,-1),0.5,colors.grey),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold")]))
    story.append(tbl); story.append(Spacer(1, 12))
    story.append(Image(bar_path, width=260, height=150)); story.append(Spacer(1, 8))
    story.append(Image(pie_path, width=200, height=200)); story.append(Spacer(1, 10))
    story.append(Paragraph("<i>Powered by SmartLinks — Turning Clicks into Clients</i>", ParagraphStyle("f", alignment=1, fontSize=10)))
    doc.build(story)

    return Response(content=buf.getvalue(), media_type="application/pdf")

@app.get("/api/report/{short_code}/csv")
def report_csv(short_code: str):
    # raw click export (for spreadsheets)
    rows = c.execute("SELECT ts, ip, user_agent, device, city, country FROM clicks WHERE short_code=? ORDER BY ts", (short_code,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["timestamp","ip","user_agent","device","city","country"])
    for r in rows: w.writerow(r)
    return Response(content=out.getvalue(), media_type="text/csv")

