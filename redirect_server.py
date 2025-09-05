# --- imports ---
import os
import stripe
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from datetime import datetime, timezone
import sqlite3
from user_agents import parse as parse_ua  # NEW
# ---

DB_PATH = os.getenv("DB_PATH", "smartlinks.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_ip(request: Request) -> str:
    # Try common proxy headers first (Render/Cloudfront/NGINX)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # could be "client, proxy1, proxy2"
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    # fallback
    client_host = request.client.host if request.client else "unknown"
    return client_host

def get_device_info(request: Request):
    ua_str = request.headers.get("user-agent", "")
    ua = parse_ua(ua_str)
    if ua.is_tablet:
        device = "tablet"
    elif ua.is_mobile:
        device = "mobile"
    elif ua.is_pc:
        device = "desktop"
    elif ua.is_bot:
        device = "bot"
    else:
        device = "unknown"
    return ua_str[:512], device  # store capped UA
