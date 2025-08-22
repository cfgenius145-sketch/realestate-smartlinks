import streamlit as st
import qrcode, requests, os
from io import BytesIO
import os, requests
BASE_REDIRECT_URL = st.secrets.get("BASE_REDIRECT_URL", os.environ.get("BASE_REDIRECT_URL", "http://127.0.0.1:8000"))


st.set_page_config(page_title="🏡 Real Estate SmartLinks", page_icon="🏡", layout="wide")
st.title("🏡 Real Estate SmartLinks")
st.write("Create **smart property links** with QR codes + analytics for your listings.")

# Where is the redirect server?
BASE_REDIRECT_URL = st.secrets.get("BASE_REDIRECT_URL", os.environ.get("BASE_REDIRECT_URL", "http://127.0.0.1:8000"))

def generate_qr(url: str):
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

url = st.text_input("Paste a property link (Zillow, MLS, Realtor.com, etc.)")

if st.button("Generate SmartLink"):
    if not url.strip():
        st.error("Please paste a property URL.")
    else:
        try:
            resp = requests.post(f"{BASE_REDIRECT_URL}/api/links", json={"original_url": url}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            short_url = data["short_url"]
            st.success(f"✅ SmartLink created: {short_url}")
            st.image(generate_qr(short_url), caption="Scan to open")
            st.download_button("⬇️ Download QR Code", data=generate_qr(short_url), file_name=f"{data['short_code']}.png", mime="image/png")
        except Exception as e:
            st.error(f"Failed to create link: {e}")

st.subheader("📊 My Property Links")
try:
    rows = requests.get(f"{BASE_REDIRECT_URL}/api/links", timeout=10).json()
    if not rows:
        st.info("No links created yet.")
    else:
        for row in rows:
            st.markdown(f"""
**Original:** {row['original_url']}  
**SmartLink:** {row['short_url']}  
**Clicks:** {row['clicks']} | **Created:** {row['created_at']}
---
""")
except Exception as e:
    st.error(f"Failed to load analytics: {e}")

