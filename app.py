# SmartLinks Streamlit frontend (ground-up)
# Streamlit Cloud: set BACKEND_URL in .streamlit/secrets.toml or type it in the UI

import os, requests, streamlit as st

DEFAULT_BACKEND = os.getenv("BACKEND_URL", st.secrets.get("BACKEND_URL", "")).rstrip("/")

st.set_page_config(page_title="SmartLinks", layout="wide")
st.title("🔗 SmartLinks – Real-Estate Smart QR & Reports")

# --------- Backend URL ----------
st.sidebar.header("Backend")
backend_url = st.sidebar.text_input("Backend URL", value=DEFAULT_BACKEND or "https://YOUR-BACKEND.onrender.com")
if "BACKEND_URL" not in st.session_state:
    st.session_state["BACKEND_URL"] = backend_url
if st.sidebar.button("Use Backend"):
    st.session_state["BACKEND_URL"] = backend_url.rstrip("/")

BACKEND = st.session_state["BACKEND_URL"]

# Health
colA, colB = st.columns(2)
with colA:
    st.subheader("Health")
    try:
        hi = requests.get(f"{BACKEND}/health", timeout=10).json()
        st.success("Backend OK")
        st.code(hi, language="json")
    except Exception as e:
        st.error(f"Health check failed. Fix Backend URL or backend deploy.\n{e}")

# --------- Workspace / Identity ----------
st.subheader("Workspace")
if "owner_token" not in st.session_state:
    st.session_state["owner_token"] = "demo-owner"

owner = st.text_input("Owner token", st.session_state["owner_token"])
st.session_state["owner_token"] = owner
email = st.text_input("Your email (for report delivery & Pro upgrade)", st.session_state.get("email", ""))
st.session_state["email"] = email

# --------- Create SmartLink ----------
st.subheader("Create a SmartLink")
url = st.text_input("Paste a property link (Zillow / MLS / YouTube / your site)")
if st.button("Create SmartLink"):
    try:
        r = requests.post(f"{BACKEND}/api/links", json={
            "owner_token": owner,
            "original_url": url,
            "email": email
        }, timeout=20)
        if r.status_code == 402:
            st.warning("Free plan limit reached — Upgrade to Pro.")
        r.raise_for_status()
        data = r.json()
        st.success("Created!")
    except Exception as e:
        st.error(f"Create failed: {e}")

# --------- Your Links ----------
st.subheader("Your SmartLinks")
try:
    resp = requests.get(f"{BACKEND}/api/links", params={"owner_token": owner}, timeout=20).json()
    links = resp.get("links", [])
    if not links:
        st.info("No links yet. Create your first one!")
    else:
        for row in links:
            with st.container(border=True):
                short_url = f"{BACKEND}/r/{row['short_code']}"
                st.write(f"**SmartLink:** {short_url}")
                st.write(f"Original: {row['original_url']}")
                st.write(f"Clicks: {row['clicks']} • Created: {row['created_at']} • Plan: {row['plan']}")

                cols = st.columns(4)
                with cols[0]:
                    st.link_button("Open", short_url)
                with cols[1]:
                    st.link_button("CSV", f"{BACKEND}/api/links/{row['id']}/clicks.csv")
                with cols[2]:
                    st.link_button("PDF", f"{BACKEND}/api/links/{row['id']}/report.pdf")
                with cols[3]:
                    st.image(f"{BACKEND}/api/links/{row['id']}/qrcode.png", caption="QR", use_container_width=False)
except Exception as e:
    st.error(f"List failed: {e}")

st.divider()

# --------- Upgrade ----------
st.subheader("Plan & Upgrade")
if st.button("Upgrade to Pro ($29/mo)"):
    try:
        r = requests.post(f"{BACKEND}/api/stripe/checkout", json={
            "owner_token": owner,
            "email": email
        }, timeout=20)
        r.raise_for_status()
        st.link_button("Complete checkout", r.json()["checkout_url"])
    except Exception as e:
        st.error(f"Checkout failed: {e}")
