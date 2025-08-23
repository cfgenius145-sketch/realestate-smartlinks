# app.py — SmartLinks for Real Estate (FULL FILE, simple + reliable)

import os
from io import BytesIO

import requests
import qrcode
import streamlit as st

# ---------------- Config ----------------
BASE_REDIRECT_URL = st.secrets.get(
    "BASE_REDIRECT_URL",
    os.environ.get("BASE_REDIRECT_URL", "http://127.0.0.1:8000"),
).rstrip("/")

STRIPE_CHECKOUT_URL = st.secrets.get(
    "STRIPE_CHECKOUT_URL",
    os.environ.get("STRIPE_CHECKOUT_URL", ""),
)

st.set_page_config(
    page_title="🏡 SmartLinks for Real Estate",
    page_icon="🏡",
    layout="wide",
)

# --------------- Helpers ----------------
def generate_qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# --------------- Header -----------------
st.markdown("# 🏡 SmartLinks for Real Estate")
st.caption("Create SmartLinks + QR codes for listings, track engagement, and wow sellers with AI reports.")

with st.expander("💳 Pricing & Plans", expanded=False):
    st.markdown(
        """
**Free** — 3 SmartLinks, basic click counts  
**Pro $29/mo** — Unlimited SmartLinks, AI Seller Reports, branded QR, priority support
"""
    )
    if STRIPE_CHECKOUT_URL:
        st.link_button("Upgrade to Pro", STRIPE_CHECKOUT_URL, use_container_width=True)
    else:
        st.info("Add STRIPE_CHECKOUT_URL in Settings → Secrets to show the Upgrade button.")

st.divider()

# --------------- Create SmartLink -------
st.subheader("🔗 Create a SmartLink")
url = st.text_input("Paste a property link (Zillow, MLS, YouTube, or your own page)")

c1, c2 = st.columns([1, 1], vertical_alignment="center")
with c1:
    if st.button("Generate SmartLink", type="primary"):
        if not url.strip():
            st.error("Please paste a property URL.")
        else:
            try:
                resp = requests.post(
                    f"{BASE_REDIRECT_URL}/api/links",
                    json={"original_url": url},
                    timeout=20,
                )
                if resp.status_code == 402:
                    st.warning("Free plan limit reached. Click 'Upgrade to Pro' to create more links.")
                resp.raise_for_status()
                data = resp.json()
                short_url = data["short_url"]
                st.success(f"✅ SmartLink created: {short_url}")

                qr_bytes = generate_qr_png(short_url)
                st.image(qr_bytes, caption="Scan to open")
                st.download_button(
                    "⬇️ Download QR Code",
                    data=qr_bytes,
                    file_name=f"{data['short_code']}.png",
                    mime="image/png",
                )
            except Exception as e:
                st.error(f"Failed to create link: {e}")

with c2:
    st.info("Tip: Use this SmartLink on flyers, open house signs, business cards, and social posts. Always share the **short** link to track clicks.")

st.divider()

# --------------- My Links + Reports -----
st.subheader("📊 My Property Links")

try:
    rows = requests.get(f"{BASE_REDIRECT_URL}/api/links", timeout=20).json()
except Exception as e:
    rows = []
    st.error(f"Failed to load links: {e}")

if not rows:
    st.info("No links created yet.")
else:
    for row in rows:
        short_url = row["short_url"]
        code = row["short_code"]

        with st.container(border=True):
            st.markdown(
                f"**Original:** {row['original_url']}  \n"
                f"**SmartLink:** {short_url}  \n"
                f"**Clicks:** {row['clicks']} &nbsp;&nbsp; | &nbsp;&nbsp; **Created:** {row['created_at']}"
            )
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                st.link_button("Open SmartLink", short_url, use_container_width=True)
            with c2:
                st.link_button("⬇️ Seller Report (PDF)", f"{BASE_REDIRECT_URL}/api/report/{code}", use_container_width=True)
            with c3:
                st.link_button("📑 Download CSV", f"{BASE_REDIRECT_URL}/api/report/{code}/csv", use_container_width=True)
