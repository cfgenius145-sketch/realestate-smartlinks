# app.py — SmartLinks Frontend (golden baseline)
import streamlit as st
import requests, secrets, string
from io import BytesIO
import qrcode
from PIL import Image

st.set_page_config(page_title="SmartLinks for Real Estate", layout="wide")

API_BASE = st.secrets.get("BASE_REDIRECT_URL", "").rstrip("/")
if not API_BASE:
    st.error("Missing BASE_REDIRECT_URL in Streamlit secrets.")
    st.stop()

# persistent owner token for this browser
def _make_token(n=24):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))
if "owner_token" not in st.session_state:
    st.session_state.owner_token = _make_token()
OWNER_HEADERS = {"X-Owner-Token": st.session_state.owner_token}

# sidebar
with st.sidebar:
    st.markdown("### SmartLinks")
    st.caption("Turn any property link into a short link + QR with analytics.")
    if st.button("🔄 Start New Workspace (clear list here)", use_container_width=True):
        st.session_state.owner_token = _make_token()
        st.rerun()
    st.caption(f"Workspace ID: …{st.session_state.owner_token[-6:]}")

# small QR
st.markdown(
    "<style>.qr-box img{max-width:220px!important;height:auto!important;}</style>",
    unsafe_allow_html=True,
)

st.title("Create a SmartLink")

url = st.text_input("Paste a property link (Zillow / MLS / YouTube / your site).",
                    placeholder="https://www.zillow.com/homedetails/123-Main-St/...")

if st.button("Generate SmartLink", type="primary"):
    if not url.strip():
        st.warning("Please paste a link first.")
    else:
        try:
            r = requests.post(f"{API_BASE}/api/links", json={"original_url": url},
                              headers=OWNER_HEADERS, timeout=15)
            if r.status_code == 200:
                st.success("✅ SmartLink created")
                data = r.json(); short_url = data["short_url"]
                with st.container(border=True):
                    st.markdown("#### SmartLink")
                    st.code(short_url, language=None)
                    c1, c2 = st.columns([1,1.2], vertical_alignment="center")
                    with c1:
                        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
                        qr.add_data(short_url); qr.make(fit=True)
                        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                        bio = BytesIO(); img.save(bio, format="PNG"); bio.seek(0)
                        st.markdown('<div class="qr-box">', unsafe_allow_html=True)
                        st.image(img, caption="QR Code")
                        st.markdown("</div>", unsafe_allow_html=True)
                    with c2:
                        st.download_button("📥 Download QR Code", data=bio, file_name="smartlink_qr.png",
                                           mime="image/png", use_container_width=True)
                        st.link_button("Open SmartLink", short_url, use_container_width=True)
            elif r.status_code == 402:
                st.error("Free plan limit reached (3 SmartLinks).")
            else:
                st.error(f"Error: {r.status_code} — {r.text}")
        except Exception as e:
            st.error(f"Request failed: {e}")

st.markdown("---")
st.subheader("My Property Links")

try:
    resp = requests.get(f"{API_BASE}/api/links", headers=OWNER_HEADERS, timeout=12)
    if resp.status_code == 200:
        links = resp.json()
        if not links:
            st.info("No links yet.")
        for item in links:
            with st.container(border=True):
                st.markdown(f"**Original:** {item['original_url']}")
                st.markdown(f"**SmartLink:** `{item['short_url']}`")
                st.caption(f"Clicks: {item['clicks']}  |  Created: {item['created_pretty']}")
                c1, c2, c3 = st.columns([1,1,1])
                with c1:
                    st.link_button("Open SmartLink", item["short_url"], use_container_width=True)
                with c2:
                    pdf_url = f"{API_BASE}/api/report/{item['short_code']}"
                    st.link_button("Open PDF (Seller Report)", pdf_url, use_container_width=True)
                with c3:
                    csv_url = f"{API_BASE}/api/report/{item['short_code']}/csv"
                    csv = requests.get(csv_url, timeout=15)
                    if csv.status_code == 200:
                        st.download_button("Download CSV", data=csv.content,
                                           file_name=f"clicks_{item['short_code']}.csv",
                                           mime="text/csv", use_container_width=True)
    else:
        st.error("Could not load links from the server.")
except Exception as e:
    st.error(f"Error loading links: {e}")
