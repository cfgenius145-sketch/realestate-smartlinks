import os
import requests
import streamlit as st

BACKEND = os.getenv("BACKEND_URL", "https://your-backend.onrender.com")

st.set_page_config(page_title="SmartLinks", layout="wide")
st.title("📊 SmartLinks Workspace")

if "owner_token" not in st.session_state:
    st.session_state["owner_token"] = st.secrets.get("OWNER_TOKEN", "demo-token")

email = st.text_input("Your email (for reports & Pro upgrade):", value=st.session_state.get("email", ""))
if email:
    st.session_state["email"] = email

url = st.text_input("Paste a property link (Zillow, MLS, YouTube...)")

def create_link(original_url):
    payload = {
        "owner_token": st.session_state["owner_token"],
        "original_url": original_url,
        "email": st.session_state.get("email", "")
    }
    r = requests.post(f"{BACKEND}/api/links", json=payload, timeout=20)
    if r.status_code == 402:
        st.warning("⚠️ Free plan limit reached. Upgrade to Pro for unlimited SmartLinks.")
        return None
    r.raise_for_status()
    return r.json()

if st.button("Create SmartLink"):
    if not url:
        st.warning("Please enter a link first")
    else:
        data = create_link(url)
        if data:
            short_url = f"{BACKEND}/r/{data['short_code']}"
            st.success(f"✅ SmartLink created: {short_url}")
            st.markdown(f"[Open SmartLink]({short_url})")

st.divider()
st.subheader("Plan & Upgrade")
if st.button("Upgrade to Pro ($29/mo)"):
    payload = {"owner_token": st.session_state["owner_token"], "email": st.session_state.get("email", "")}
    r = requests.post(f"{BACKEND}/api/stripe/checkout", json=payload, timeout=20)
    r.raise_for_status()
    st.markdown(f"[Complete checkout here]({r.json()['checkout_url']})")
