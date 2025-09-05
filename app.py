# SmartLinks Streamlit frontend (clean MVP)
# Streamlit Cloud: put BACKEND_URL in .streamlit/secrets.toml

import os, requests, streamlit as st

st.set_page_config(page_title="SmartLinks", layout="wide")
st.title("🔗 SmartLinks – Real-Estate Smart QR & Reports")

DEFAULT_BACKEND = os.getenv("BACKEND_URL", st.secrets.get("BACKEND_URL", "")).rstrip("/")
backend_input = st.sidebar.text_input("Backend URL", value=DEFAULT_BACKEND or "https://realestate-smartlinks.onrender.com")
if st.sidebar.button("Use Backend"):
    st.session_state["BACKEND_URL"] = backend_input.rstrip("/")

BACKEND = st.session_state.get("BACKEND_URL", backend_input.rstrip("/"))

# Health
st.subheader("Health")
try:
    health = requests.get(f"{BACKEND}/health", timeout=10).json()
    st.success("Backend OK")
    st.code(health, language="json")
except Exception as e:
    st.error(f"Health check failed. Fix BACKEND_URL or backend deploy.\n{e}")

# Workspace
st.subheader("Workspace")
if "owner_token" not in st.session_state:
    st.session_state["owner_token"] = "demo-owner"
owner = st.text_input("Owner token", st.session_state["owner_token"])
st.session_state["owner_token"] = owner
email = st.text_input("Your email (optional, for future Pro)")
st.session_state["email"] = email

# Create
st.subheader("Create a SmartLink")
url = st.text_input("Paste a property link (Zillow / MLS / YouTube / your site)")
if st.button("Create SmartLink"):
    try:
        r = requests.post(f"{BACKEND}/api/links", json={
            "owner_token": owner, "original_url": url, "email": email
        }, timeout=20)
        if r.status_code == 402:
            st.warning("Free plan limit reached (3).")
        r.raise_for_status()
        st.success("SmartLink created.")
    except Exception as e:
        st.error(f"Create failed: {e}")

# List
st.subheader("Your SmartLinks")
try:
    resp = requests.get(f"{BACKEND}/api/links", params={"owner_token": owner}, timeout=20)
    resp.raise_for_status()
    links = resp.json().get("links", [])
    if not links:
        st.info("No links yet.")
    for row in links:
        with st.container(border=True):
            short_url = f"{BACKEND}/r/{row['short_code']}"
            st.write(f"**SmartLink:** {short_url}")
            st.write(f"Original: {row['original_url']}")
            st.write(f"Clicks: {row['clicks']} • Created: {row['created_at']} • Plan: {row['plan']}")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.link_button("Open", short_url)
            with c2:
                st.link_button("CSV", f"{BACKEND}/api/links/{row['id']}/clicks.csv")
            with c3:
                st.link_button("PDF", f"{BACKEND}/api/links/{row['id']}/report.pdf")
            with c4:
                st.image(f"{BACKEND}/api/links/{row['id']}/qrcode.png", caption="QR", use_container_width=False)
except Exception as e:
    st.error(f"List failed: {e}")
