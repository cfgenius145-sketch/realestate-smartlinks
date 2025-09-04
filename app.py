# app.py — SmartLinks frontend (classic clean UI + sidebar + sane QR size)

import streamlit as st
import requests
from io import BytesIO
import qrcode
from PIL import Image

# ---------- Page setup ----------
st.set_page_config(page_title="SmartLinks for Real Estate", layout="wide")

API_BASE = st.secrets.get(
    "BASE_REDIRECT_URL", "https://realestate-smartlinks.onrender.com"
).rstrip("/")
STRIPE_URL = st.secrets.get("STRIPE_CHECKOUT_URL")

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### SmartLinks")
    st.caption("Turn any property link into a short link + QR with analytics.")
    if STRIPE_URL:
        st.link_button("🚀 Upgrade to Pro", STRIPE_URL, use_container_width=True)
    st.divider()
    st.markdown(
        "- Paste a **Zillow/MLS/YouTube/your site** link\n"
        "- Click **Generate SmartLink**\n"
        "- Print or share the QR\n"
        "- Click **Generate Seller Report** to download a PDF"
    )
    st.caption("Free plan limited to **3 SmartLinks** per IP.")

# ---------- Small CSS to keep QR reasonable ----------
st.markdown(
    """
    <style>
    .qr-box img {max-width: 260px !important; height: auto !important;}
    .result-card {background: #F8FAFC; border: 1px solid #E5E7EB; border-radius: 10px; padding: 16px;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Create a SmartLink")

# ---------- Input ----------
url = st.text_input(
    "Paste a property link (Zillow / MLS / YouTube / your site).",
    placeholder="https://www.zillow.com/homedetails/123-Main-St/...",
)

generate = st.button("Generate SmartLink", type="primary")

# ---------- Create link ----------
if generate:
    if not url.strip():
        st.warning("Please paste a link first.")
    else:
        try:
            r = requests.post(f"{API_BASE}/api/links", json={"original_url": url}, timeout=15)
            if r.status_code == 200:
                st.success("✅ SmartLink created")
                data = r.json()
                short_url = data["short_url"]

                # Result block
                with st.container(border=True):
                    st.markdown("#### SmartLink")
                    st.code(short_url, language=None)

                    # 2 columns: QR + actions
                    col_qr, col_actions = st.columns([1, 1.2], vertical_alignment="center")

                    # --- QR (kept small with CSS) ---
                    with col_qr:
                        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
                        qr.add_data(short_url)
                        qr.make(fit=True)
                        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                        bio = BytesIO()
                        img.save(bio, format="PNG")
                        bio.seek(0)
                        qr_holder = st.container()
                        with qr_holder:
                            st.markdown('<div class="qr-box">', unsafe_allow_html=True)
                            st.image(img, caption="QR Code")
                            st.markdown("</div>", unsafe_allow_html=True)

                    # --- Actions ---
                    with col_actions:
                        st.download_button(
                            "📥 Download QR Code",
                            data=bio,
                            file_name="smartlink_qr.png",
                            mime="image/png",
                            use_container_width=True,
                        )
                        st.link_button("Open SmartLink", short_url, use_container_width=True)

            elif r.status_code == 402:
                st.error("Free plan limit reached (3 SmartLinks). Upgrade to Pro to continue.")
            else:
                st.error(f"Error: {r.status_code} — {r.text}")
        except Exception as e:
            st.error(f"Request failed: {e}")

st.markdown("---")
st.subheader("My Property Links")

# ---------- List links ----------
try:
    resp = requests.get(f"{API_BASE}/api/links", timeout=10)
    if resp.status_code == 200:
        links = resp.json()
        if not links:
            st.info("No links yet.")
        for item in links:
            with st.container(border=True):
                st.markdown(f"**Original:** {item['original_url']}")
                st.markdown(f"**SmartLink:** `{item['short_url']}`")
                st.caption(f"Clicks: {item['clicks']}  |  Created: {item['created_pretty']}")

                c1, c2, c3 = st.columns([1, 1, 1])
                with c1:
                    st.link_button("Open SmartLink", item["short_url"], use_container_width=True)
                with c2:
                    if st.button(
                        "Generate Seller Report (PDF)",
                        key=f"pdf_{item['short_code']}",
                        use_container_width=True,
                    ):
                        pdf = requests.get(f"{API_BASE}/api/report/{item['short_code']}", timeout=25)
                        if pdf.status_code == 200:
                            st.download_button(
                                "⬇️ Download Report (PDF)",
                                data=pdf.content,
                                file_name=f"seller_report_{item['short_code']}.pdf",
                                mime="application/pdf",
                                use_container_width=True,
                            )
                        else:
                            st.error("Report failed.")
                with c3:
                    csv = requests.get(
                        f"{API_BASE}/api/report/{item['short_code']}/csv", timeout=20
                    )
                    if csv.status_code == 200:
                        st.download_button(
                            "Download CSV (raw clicks)",
                            data=csv.content,
                            file_name=f"clicks_{item['short_code']}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
    else:
        st.error("Could not load links from the server.")
except Exception as e:
    st.error(f"Error loading links: {e}")

st.markdown("---")
st.subheader("Pricing & Plans")
st.write("**Free:** 3 SmartLinks • QR codes • AI Seller Reports (PDF) • CSV export")
st.write("**Pro ($29/mo):** Unlimited SmartLinks • Priority support")
if STRIPE_URL:
    st.link_button("🚀 Upgrade to Pro", STRIPE_URL, use_container_width=True)
