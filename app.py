import os, requests, streamlit as st

BACKEND = os.getenv("BACKEND_URL", st.secrets.get("BACKEND_URL", "")) or "https://YOUR-BACKEND.onrender.com"

st.set_page_config(page_title="SmartLinks", layout="wide")
st.title("SmartLinks – Minimal Tester")

# Health
try:
    h = requests.get(f"{BACKEND}/health", timeout=10).json()
    st.success(f"Backend OK: {h}")
except Exception as e:
    st.error(f"Backend /health failed: {e}")

# Workspace
if "owner_token" not in st.session_state:
    st.session_state["owner_token"] = "demo-owner"

email = st.text_input("Your email (optional)", st.session_state.get("email",""))
st.session_state["email"] = email

url = st.text_input("Property URL")
if st.button("Create SmartLink"):
    try:
        r = requests.post(f"{BACKEND}/api/links", json={
            "owner_token": st.session_state["owner_token"],
            "original_url": url,
            "email": email
        }, timeout=20)
        if r.status_code == 402:
            st.warning("Free tier limit reached. Click Upgrade to Pro.")
        r.raise_for_status()
        data = r.json()
        short_url = f"{BACKEND}/r/{data['short_code']}"
        st.success(f"Created: {short_url}")
        st.markdown(f"[Open SmartLink]({short_url})")
    except Exception as e:
        st.error(f"Create failed: {e}")

st.divider()
if st.button("Upgrade to Pro ($29/mo)"):
    try:
        r = requests.post(f"{BACKEND}/api/stripe/checkout", json={
            "owner_token": st.session_state["owner_token"],
            "email": email
        }, timeout=20)
        r.raise_for_status()
        st.markdown(f"[Checkout link]({r.json()['checkout_url']})")
    except Exception as e:
        st.error(f"Checkout failed: {e}")
