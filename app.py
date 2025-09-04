# app.py — Streamlit frontend (stable UI + QR preview + no deprecation)

import streamlit as st
import requests
from io import BytesIO
import qrcode
from PIL import Image

st.set_page_config(page_title="SmartLinks for Real Estate", layout="wide")

API_BASE = st.secrets.get("BASE_REDIRECT_URL", "https://realestate-smartlinks.onrender.com").rstrip("/")

st.header("Create a SmartLink")
url = st.text_input("Paste a property link (Zillow / MLS / YouTube / your site).")

if st.button("Generate SmartLink", type="primary"):
    if not url:
        st.warning("Please paste a link first.")
    else:
        try:
            r = requests.post(f"{API_BASE}/api/links", json={"original_url": url}, timeout=15)
            if r.status_code == 200:
                st.success("✅ SmartLink created")
                data = r.json()
                short_url = data["short_url"]
                st.text_input("SmartLink", value=short_url, label_visibility="collapsed")

                # QR preview + download
                qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
                qr.add_data(short_url); qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                b = BytesIO(); img.save(b, format="PNG"); b.seek(0)

                c1, c2 = st.columns([2,1])
                with c1:
                    st.image(img, caption="QR Code", use_container_width=True)
                with c2:
                    st.download_button("📥 Download QR Code", data=b, file_name="smartlink_qr.png",
                                       mime="image/png", use_container_width=True)
                    st.link_button("Open SmartLink", short_url, use_container_width=True)
            else:
                st.error(f"Error: {r.status_code} — {r.text}")
        except Exception as e:
            st.error(f"Request failed: {e}")

st.markdown("---")
st.subheader("My Property Links")

try:
    resp = requests.get(f"{API_BASE}/api/links", timeout=10)
    if resp.status_code == 200:
        links = resp.json()
        if not links:
            st.info("No links yet.")
        for item in links:
            st.write(f"**Original:** {item['original_url']}")
            st.write(f"**SmartLink:** {item['short_url']}")
            st.caption(f"Clicks: {item['clicks']}  |  Created: {item['created_pretty']}")
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                st.link_button("Open SmartLink", item["short_url"], use_container_width=True)
            with c2:
                if st.button("Generate Seller Report (PDF)", key=f"pdf_{item['short_code']}", use_container_width=True):
                    pdf = requests.get(f"{API_BASE}/api/report/{item['short_code']}", timeout=20)
                    if pdf.status_code == 200:
                        st.download_button("⬇️ Download Report (PDF)", data=pdf.content,
                                           file_name=f"seller_report_{item['short_code']}.pdf",
                                           mime="application/pdf", use_container_width=True)
                    else:
                        st.error("Report failed.")
            with c3:
                csv = requests.get(f"{API_BASE}/api/report/{item['short_code']}/csv", timeout=20)
                if csv.status_code == 200:
                    st.download_button("Download CSV (raw clicks)", data=csv.content,
                                       file_name=f"clicks_{item['short_code']}.csv",
                                       mime="text/csv", use_container_width=True)
    else:
        st.error("Could not load links.")
except Exception as e:
    st.error(f"Error loading links: {e}")

st.markdown("---")
st.subheader("Pricing & Plans")
st.write("**Free:** 3 SmartLinks • QR codes • AI Seller Reports (PDF) • CSV export")
st.write("**Pro ($29/mo):** Unlimited SmartLinks • Priority support")
stripe_url = st.secrets.get("STRIPE_CHECKOUT_URL")
if stripe_url:
    st.link_button("🚀 Upgrade to Pro", stripe_url, use_container_width=True)
