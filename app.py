# app.py — SmartLinks for Real Estate (BRANDED POLISH EDITION — FIXED)

import os
from io import BytesIO

import requests
import qrcode
import streamlit as st

# ---------------- BRAND SETTINGS (edit these 3 lines) ----------------
BRAND_NAME = "SmartLinks"
LOGO_URL = "https://raw.githubusercontent.com/google/material-design-icons/master/png/action/qr_code_2/materialicons/48dp/2x/outline_qr_code_2_black_48dp.png"  # replace with your logo URL
PRIMARY_HEX = "#0EA5E9"  # teal/blue you picked for the site
# --------------------------------------------------------------------

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
    page_title=f"🏡 {BRAND_NAME} — Real Estate",
    page_icon="🏡",
    layout="wide",
)

# ---------------- Light CSS polish ----------------
st.markdown(
    f"""
    <style>
      :root {{ --brand: {PRIMARY_HEX}; }}
      .brand-badge {{
        display:inline-flex;align-items:center;gap:.5rem;
        padding:.35rem .6rem;border-radius:999px;
        background:rgba(14,165,233,.12);color:#0e7490;font-weight:600;font-size:.85rem;
      }}
      .brand-cta > button, .brand-cta a {{
        border-radius:12px !important; font-weight:600 !important;
      }}
      .card {{
        border:1px solid rgba(0,0,0,.06); border-radius:16px; padding:16px; background:rgba(255,255,255,.7);
      }}
      .muted {{ color:#6b7280; font-size:.9rem; }}
      .linkbox input {{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- Helpers ----------------
def generate_qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def get_links():
    try:
        return requests.get(f"{BASE_REDIRECT_URL}/api/links", timeout=20).json()
    except Exception as e:
        st.error(f"Failed to load links: {e}")
        return []

# ---------------- Sidebar ----------------
with st.sidebar:
    st.image(LOGO_URL, width=64)
    st.markdown(f"### {BRAND_NAME}")
    st.caption("QR + Short Links + AI Seller Reports")

    # “Login-lite” email capture (remember only; no passwords)
    if "agent_email" not in st.session_state:
        st.session_state.agent_email = ""

    st.session_state.agent_email = st.text_input(
        "Your email (for Pro/unlock)",
        value=st.session_state.agent_email,
        placeholder="you@brokerage.com",
        help="We’ll use this when auto-upgrade via Stripe is enabled (no password needed).",
    )

    if st.session_state.agent_email.strip():
        st.markdown(f"<span class='brand-badge'>Signed in as {st.session_state.agent_email}</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span class='brand-badge'>Guest · Free (3 links)</span>", unsafe_allow_html=True)

    st.divider()
    st.markdown("### Plans")
    st.markdown("**Free** — 3 SmartLinks\n\n**Pro** — Unlimited, priority support")
    if STRIPE_CHECKOUT_URL:
        st.link_button("🚀 Upgrade to Pro", STRIPE_CHECKOUT_URL, use_container_width=True)
    else:
        st.caption("Add STRIPE_CHECKOUT_URL in Secrets to enable Upgrade button.")

# ---------------- Header ----------------
col_logo, col_title, col_cta = st.columns([0.1, 0.7, 0.2])
with col_logo:
    st.image(LOGO_URL, width=48)
with col_title:
    st.markdown(f"## 🏡 {BRAND_NAME} for Real Estate")
    st.caption("Create SmartLinks + QR, track engagement, and wow sellers with AI reports.")
with col_cta:
    st.markdown("<div class='brand-cta'>", unsafe_allow_html=True)
    # This button just scrolls user attention; no in-page anchors in Streamlit
    st.button("✨ Try Free (3 links)", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

st.divider()

# ---------------- Create SmartLink ----------------
st.markdown("### 🔗 Create a SmartLink")
st.caption("Paste a property link (Zillow / MLS / YouTube / your site).")

with st.container():
    url = st.text_input("Property URL", placeholder="https://www.zillow.com/homedetails/...", label_visibility="collapsed")
    c1, c2 = st.columns([1, 1], vertical_alignment="center")
    with c1:
        if st.button("Generate SmartLink", type="primary"):
            if not url.strip():
                st.error("Please paste a property URL.")
            else:
                try:
                    headers = {}
                    if st.session_state.agent_email.strip():
                        headers["X-User-Email"] = st.session_state.agent_email.strip()

                    resp = requests.post(
                        f"{BASE_REDIRECT_URL}/api/links",
                        json={"original_url": url},
                        headers=headers,
                        timeout=20,
                    )
                    if resp.status_code == 402:
                        st.warning("Free plan limit reached. Click 'Upgrade to Pro' to create more links.")
                    resp.raise_for_status()
                    data = resp.json()
                    short_url = data["short_url"]

                    with st.container():
                        st.success("✅ SmartLink created")
                        st.text_input("SmartLink", value=short_url, key=f"short_{data['short_code']}")
                        qr_bytes = generate_qr_png(short_url)
                        qc1, qc2 = st.columns([1, 1])
                        with qc1:
                            st.image(qr_bytes, caption="Scan to open", use_column_width=True)
                        with qc2:
                            st.download_button(
                                "⬇️ Download QR Code",
                                data=qr_bytes,
                                file_name=f"{data['short_code']}.png",
                                mime="image/png",
                                use_container_width=True,
                            )
                            st.link_button("Open SmartLink", short_url, use_container_width=True)
                except Exception as e:
                    st.error(f"Failed to create link: {e}")

    with c2:
        st.markdown(
            """
            **Tips**
            - Use on open house flyers, yard signs, business cards  
            - Post the SmartLink on IG/FB/YouTube  
            - Always share the **short** link to track clicks
            """
        )
st.divider()

# ---------------- My Links + Reports ----------------
st.markdown("### 📊 My Property Links")

rows = get_links()
if not rows:
    st.info("No links created yet.")
else:
    for row in rows:
        short_url = row["short_url"]
        code = row["short_code"]
        created_pretty = row.get("created_pretty") or row.get("created_at") or "-"
        clicks = row["clicks"]

        st.markdown(
            f"""
            <div class="card">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;">
                <div>
                  <div><strong>Original:</strong> {row['original_url']}</div>
                  <div><strong>SmartLink:</strong> <a href="{short_url}" target="_blank">{short_url}</a></div>
                  <div class="muted">Created: {created_pretty} · Clicks: {clicks}</div>
                </div>
                <div style="display:flex;gap:.5rem;flex-wrap:wrap;">
                  <a href="{short_url}" target="_blank"><button>Open</button></a>
                  <a href="{BASE_REDIRECT_URL}/api/report/{code}" target="_blank"><button>⬇️ PDF Report</button></a>
                  <a href="{BASE_REDIRECT_URL}/api/report/{code}/csv" target="_blank"><button>📑 CSV</button></a>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
