"""
Supabase Database Integration for Meesho E2E Engine.
Provides optional cloud storage for account credentials, sessions, and reports
when deployed on platforms like Render where local JSON files are excluded via .gitignore.

Configuration:
Set the following environment variables:
- SUPABASE_URL: Your Supabase project URL (e.g., https://xyzcompany.supabase.co)
- SUPABASE_KEY: Your Supabase anon or service-role API key
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("SupabaseDB")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()

_supabase_client = None

def get_supabase_client():
    """Lazily initializes and returns the Supabase client if configured."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    try:
        from supabase import create_client, Client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Connected to Supabase project successfully.")
        return _supabase_client
    except Exception as e:
        logger.warning(f"Could not initialize Supabase client: {e}")
        return None

def is_supabase_enabled() -> bool:
    """Returns True if Supabase credentials are configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY)

def fetch_accounts_from_supabase() -> List[Dict[str, Any]]:
    """
    Fetches active accounts stored in the Supabase 'meesho_accounts' table.
    Filters out any blacklisted numbers.
    """
    client = get_supabase_client()
    if not client:
        return []

    try:
        res = client.table("meesho_accounts").select("*").execute()
        rows = res.data or []
        clean_accounts = []
        for row in rows:
            mobile = str(row.get("mobile") or row.get("phone") or "")
            if "7977748514" in mobile:
                continue
            clean_accounts.append(row)
        return clean_accounts
    except Exception as e:
        logger.error(f"Error fetching accounts from Supabase: {e}")
        return []

def upsert_account_to_supabase(account_data: Dict[str, Any]) -> bool:
    """
    Upserts an account session into the 'meesho_accounts' table.
    """
    mobile = str(account_data.get("mobile") or account_data.get("phone") or "")
    if "7977748514" in mobile:
        logger.warning("Refusing to upload blacklisted personal account to Supabase.")
        return False

    client = get_supabase_client()
    if not client:
        return False

    try:
        record = {
            "mobile": mobile,
            "user_id": str(account_data.get("user_id") or ""),
            "xo": account_data.get("xo", ""),
            "ox": account_data.get("ox", ""),
            "raw_json": json.dumps(account_data),
            "updated_at": "now()"
        }
        client.table("meesho_accounts").upsert(record, on_conflict="mobile").execute()
        return True
    except Exception as e:
        logger.error(f"Error upserting account to Supabase: {e}")
        return False

