# app.py
# SmartLinks – Streamlit frontend with email accounts + Stripe upgrade
import os
import time
import requests
import streamlit as st

# ---- CONFIG ----
st.set_page_config(page_title="SmartLinks for Real Estate", page_icon="🔗", layout="wide")

# Resolve backend base URL (Secrets > BACKEND_BASE recommended)
BACKEND_BASE = st.secrets.get("BACKEND_BASE", os.getenv("BACKEND_BASE", "http://localhost:8000"))

# Small helper to call backend safely
def api_get(path, params=None, timeout=12):
    r = requests.get(f"{BACKEND_BASE}{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def api_post(path, json=None, timeout=12):
    r = requests.post(f"{BACKEND_BASE}{path}", json=json, timeout=timeout)
    r.raise_for_status()
    return r.json()

# ---- SIDEBAR: ACCOUNT & PLAN ----
with st.sidebar:
    st.markdown("### 👤 Your Account")
    st.caption(f"🔧 Backend: {BACKEND_BASE}")

    if "owner" not in st.session_state:
        st.session_state.owner = {"owner_id": None, "plan": "free"}

    email = st.text_input("Work email", placeholder="agent@broker.com")

    if st.button("Sign in / Continue", use_container_width=True):
        if not email:
            st.error("Enter your email first.")
        else:
            try:
                info = api_post("/api/owner/register", {"email": email})
                st.session_state.owner = info
                st.success(f"Signed in. Plan: {info['plan']}")
            except requests.HTTPError as he:
                st.error(f"Could not register: {he.response.text}")
            except Exception as e:
                st.error(f"Could not register: {e}")

    # Always try to refresh plan if we have an owner_id
    if st.session_state.owner["owner_id"]:
        try:
            status = api_get("/api/plan/status", {"owner_id": st.session_state.owner["owner_id"]})
            st.session_state.owner["plan"] = status.get("plan", "free")
        except Exception:
            pass

        plan = st.session_state.owner["plan"]
        st.markdown(f"**Plan:** {'🟢 Pro' if plan=='pro' else '🆓 Free'}")

        if plan == "free":
            if st.button("Upgrade to Pro ($29/mo)", type="primary", use_container_width=True):
                try:
                    out = api_post("/api/stripe/create-checkout-session",
                                   {"owner_id": st.session_state.owner["owner_id"]})
                    st.link_button("Open Stripe Checkout", out["url"], use_container_width=True)
                    st.info("After payment, come back and click Refresh Status.")
                except requests.HTTPError as he:
                    st.error(f"Checkout error: {he.response.text}")
                except Exception as e:
                    st.error(f"Checkout error: {e}")

        if st.button("Refresh Status", use_container_width=True):
            st.experimental_rerun()

# ---- MAIN ----
st.title("SmartLinks for Real Estate")
if st.session_state.owner["owner_id"]:
    st.caption(f"Owner ID: `{st.session_state.owner['owner_id']}`")
else:
    st.warning("Enter your email in the sidebar to start. Free plan gives you 3 SmartLinks.")

st.divider()

# --- Create SmartLink UI (minimal) ---
st.subheader("Create a SmartLink")
url = st.text_input("Paste a property URL (Zillow/MLS/YouTube/your site)")
slug = st.text_input("Custom slug (optional)", placeholder="e.g., 1234-oak-ave")

def create_smartlink(url: str, maybe_slug: str | None):
    if not st.session_state.owner["owner_id"]:
        st.error("Please sign in with your email first.")
        return
    try:
        data = {"owner_id": st.session_state.owner["owner_id"], "url": url, "slug": (maybe_slug or None)}
        res = api_post("/api/links/create", data)
        short = res.get("short_url", "")
        st.success(f"SmartLink created: {BACKEND_BASE.rstrip('/')}{short}")
    except requests.HTTPError as he:
        st.error(he.response.text)
    except Exception as e:
        st.error(f"Create failed: {e}")

colA, colB = st.columns([1,1])
with colA:
    if st.button("Create SmartLink", type="primary", use_container_width=True):
        if not url:
            st.error("Please paste a URL.")
        else:
            create_smartlink(url, slug)

st.info("Pro plan removes the 3-link cap and unlocks unlimited SmartLinks.")
