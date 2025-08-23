# app.py — full drop-in Streamlit UI

import os, requests, qrcode
import streamlit as st
from io import BytesIO

# Config
BASE_REDIRECT_URL = st.secrets.get("BASE_REDIRECT_URL", os.environ.get("BASE_REDIRECT_URL", "http://127.0.0.1:8000"))
STRIPE_CHECKOUT_URL = st.secrets.get("STRIPE_CHECKOUT_URL", os.environ.get("STRIPE_CHECKOUT_URL", ""))

st.set_page_config(page_title="🏡 SmartLinks for Real Estate", page_icon="🏡", layout="wide")

def generate_qr(url: str):
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# Header & Pricing
st.markdown("# 🏡 SmartLinks for Real Estate")
st.caption("Create SmartLinks + QR codes for listings, track engagement, and wow sellers with AI reports.")

with st.expander("💳 Pricing & Plans", expanded=False):
    st.markdown("""
**Free** — 3 SmartLinks, basic click counts  
**Pro $29/mo** — Unlimited SmartLinks, AI Seller Reports, branded QR, priority support
""")
    if STRIPE_CHECKOUT_URL:
        st.link_button("Upgrade to Pro", STRIPE_CHECKOUT_URL, use_container_width=True)
    else:
        st.info("Add STRIPE_CHECKOUT_URL in Secrets to enable the Upgrade button.")

# Create SmartLink
st.subheader("🔗 Create a SmartLink")
url = st.text_input("Paste a property link (Zillow, MLS, your own page, YouTube, etc.)")

col_a, col_b = st.columns([1,1])
with col_a:
    if st.button("Generate SmartLink"):
        if not url.strip():
            st.error("Please paste a property URL.")
        else:
            try:
                resp = requests.post(f"{BASE_REDIRECT_URL}/api/links", json={"original_url": url}, timeout=15)
                if resp.status_code == 402:
                    st.warning("Free plan limit reached. Click 'Upgrade to Pro' to create more links.")
                resp.raise_for_status()
                data = resp.json()
                short_url = data["short_url"]
                st.success(f"✅ SmartLink created: {short_url}")
                qr_bytes = generate_qr(short_url)
                st.image(qr_bytes, caption="Scan to open")
                st.download_button("⬇️ Download QR Code", data=qr_bytes, file_name=f"{data['short_code']}.png", mime="image/png")
            except Exception as e:
                st.error(f"Failed to create link: {e}")
with col_b:
    st.info("Tip: You can point SmartLinks to your **own landing page** to keep buyers on your brand.")

# List links + Seller Report
st.subheader("📊 My Property Links")
try:
    rows = requests.get(f"{BASE_REDIRECT_URL}/api/links", timeout=15).json()
    if not rows:
        st.info("No links created yet.")
    else:
        for row in rows:
            short_url = row["short_url"]
            code = row["short_code"]
            with st.container(border=True):
                st.markdown(f"**Original:** {row['original_url']}  \n**SmartLink:** {short_url}  \n**Clicks:** {row['clicks']} | **Created:** {row['created_at']}")
                c1, c2 = st.columns([1,1])
                with c1:
                    st.link_button("Open SmartLink", short_url)
                with c2:
                    if st.button("Generate Seller Report (PDF)", key=f"report_{code}"):
                        r = requests.get(f"{BASE_REDIRECT_URL}/api/report/{code}", timeout=30)
                        if r.status_code == 200:
                            st.download_button(
                                label="⬇️ Download Report (PDF)",
                                data=r.content,
                                file_name=f"Seller_Report_{code}.pdf",
                                mime="application/pdf",
                                key=f"dl_{code}"
                            )
                        else:
                            st.error(f"Report failed: {r.text}")
except Exception as e:
    st.error(f"Failed to load analytics: {e}")
