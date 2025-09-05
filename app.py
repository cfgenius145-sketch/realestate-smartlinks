# app.py (Streamlit)
import os, requests, time
import streamlit as st

BACKEND_BASE = os.getenv("BACKEND_BASE", "https://your-render-backend.onrender.com")

st.set_page_config(page_title="SmartLinks", page_icon="🔗", layout="wide")

# ---- Account section ----
with st.sidebar:
    st.markdown("### 👤 Your Account")
    email = st.text_input("Work email", placeholder="agent@broker.com")
    if "owner" not in st.session_state:
        st.session_state.owner = {"owner_id": None, "plan": "free"}

    if email:
        if st.button("Sign in / Continue", use_container_width=True):
            try:
                r = requests.post(f"{BACKEND_BASE}/api/owner/register", json={"email": email}, timeout=10)
                r.raise_for_status()
                st.session_state.owner = r.json()
                st.success(f"Signed in. Plan: {st.session_state.owner['plan']}")
            except Exception as e:
                st.error(f"Could not register: {e}")

    if st.session_state.owner["owner_id"]:
        # Plan status
        try:
            status = requests.get(f"{BACKEND_BASE}/api/plan/status",
                                  params={"owner_id": st.session_state.owner["owner_id"]},
                                  timeout=10).json()
            st.session_state.owner["plan"] = status.get("plan", "free")
        except Exception:
            pass

        plan = st.session_state.owner["plan"]
        st.markdown(f"**Plan:** {'🟢 Pro' if plan=='pro' else '🆓 Free'}")

        if plan == "free":
            if st.button("Upgrade to Pro ($29/mo)", type="primary", use_container_width=True):
                try:
                    r = requests.post(f"{BACKEND_BASE}/api/stripe/create-checkout-session",
                                      json={"owner_id": st.session_state.owner["owner_id"]},
                                      timeout=10)
                    checkout_url = r.json()["url"]
                    st.link_button("Open Checkout", checkout_url, use_container_width=True)
                    st.info("After payment, come back here and click Refresh Status.")
                except Exception as e:
                    st.error(f"Could not start checkout: {e}")
        if st.button("Refresh Status", use_container_width=True):
            st.experimental_rerun()

# ---- Main UI header ----
st.title("SmartLinks for Real Estate")
if st.session_state.owner["owner_id"]:
    st.caption(f"Owner ID: `{st.session_state.owner['owner_id']}`")
else:
    st.warning("Enter your email in the sidebar to start. Free plan gives you 3 SmartLinks.")

# ---- Wherever you create links, attach owner_id and enforce plan ----
def create_smartlink(url: str, slug: str | None = None):
    owner_id = st.session_state.owner["owner_id"]
    if not owner_id:
        st.error("Please sign in with your email first.")
        return None
    try:
        r = requests.post(f"{BACKEND_BASE}/api/links/create",
                          json={"owner_id": owner_id, "url": url, "slug": slug}, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as he:
        st.error(he.response.text)
    except Exception as e:
        st.error(f"Create failed: {e}")
    return None

# --- your existing UI below ---
# e.g. input box for URL, button to create, etc.
