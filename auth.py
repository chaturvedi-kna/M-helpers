"""
Authentication and Session Manager for Meesho E2E Engine.
Handles Account JSON loading, header generation, and headless token refresh.
"""

import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import requests

from config import (
    DEFAULT_API_HOST,
    DEFAULT_AUTHORIZATION,
    DEFAULT_SHIELD_SESSION_ID,
    APP_VERSION,
    APP_VERSION_CODE,
    APP_SDK_VERSION,
    DEFAULT_TELEGRAM_API_ID,
    DEFAULT_TELEGRAM_API_HASH,
    BOT_USERNAME,
)


def load_account_json(file_path_or_dict: Any) -> Dict[str, Any]:
    """Loads account JSON from file path, raw JSON string, or dict."""
    if isinstance(file_path_or_dict, dict):
        return dict(file_path_or_dict)
    elif isinstance(file_path_or_dict, (str, Path)):
        p = Path(file_path_or_dict)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        # Maybe raw JSON string
        try:
            return json.loads(str(file_path_or_dict))
        except Exception:
            raise FileNotFoundError(f"Account JSON file not found: {file_path_or_dict}")
    raise ValueError(f"Invalid account parameter: {type(file_path_or_dict)}")


def save_account_json(account_data: Dict[str, Any], file_path: str, backup: bool = True):
    """Persists refreshed account data safely to file using atomic replace."""
    from account_manager import atomic_save_account_json
    atomic_save_account_json(account_data, file_path, create_backup=backup)



def build_meesho_headers(account_data: Dict[str, Any]) -> Dict[str, str]:
    """Constructs authenticated Meesho mobile app headers."""
    identity = account_data.get("identity") or {}
    user_id = str(account_data.get("user_id") or "")
    mobile = account_data.get("mobile") or ""
    phone = account_data.get("phone") or (f"+91{mobile}" if mobile else "")

    u_token = base64.b64encode(phone.encode()).decode() if phone else ""
    auth = account_data.get("authorization") or identity.get("authorization") or DEFAULT_AUTHORIZATION
    shield_id = account_data.get("shield_session_id") or identity.get("shield_session_id") or DEFAULT_SHIELD_SESSION_ID
    xo = str(account_data.get("xo") or "")

    # Align instance_id with embedded instance_id in XO token JWT payload
    jwt_instance_id = None
    if xo:
        try:
            parts = xo.split(".")
            if len(parts) >= 2:
                pad = parts[1] + "=" * (-len(parts[1]) % 4)
                outer = json.loads(base64.urlsafe_b64decode(pad.encode()))
                jwt_str = outer.get("jwt", "")
                if "." in jwt_str:
                    jwt_p = jwt_str.split(".")[1]
                    jwt_pad = jwt_p + "=" * (-len(jwt_p) % 4)
                    raw_payload = base64.urlsafe_b64decode(jwt_pad.encode()).decode("utf-8", errors="replace")
                    try:
                        jwt_payload = json.loads(raw_payload)
                        jwt_instance_id = jwt_payload.get("https://meesho.com/instance_id")
                    except Exception:
                        match = re.search(r'"https://meesho\.com/instance_id"\s*:\s*"([^"]+)"', raw_payload)
                        if match:
                            jwt_instance_id = match.group(1)
        except Exception:
            pass

    instance_id = jwt_instance_id or str(account_data.get("instance_id") or identity.get("instance_id") or uuid.uuid4().hex)
    app_session_id = str(identity.get("app_session_id") or uuid.uuid4().hex)
    gaid = str(account_data.get("gaid") or identity.get("gaid") or "")

    headers = {
        "Authorization": auth,
        "App-Version": APP_VERSION,
        "App-Version-Code": APP_VERSION_CODE,
        "Instance-Id": instance_id,
        "Country-Iso": "in",
        "Application-Id": "com.meesho.supply",
        "App-Session-Id": app_session_id,
        "App-Sdk-Version": APP_SDK_VERSION,
        "App-Client-Id": "android",
        "App-Iso-Language-Code": "en",
        "Shield-Session-Id": shield_id,
        "Xo": xo,
        "App-User-Id": user_id,
        "U-Token": u_token,
        "Meesho-User-Context": "logged_in" if xo else "anonymous",
        "X-Wishlist-Aggregation-Required": "false",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/4.9.0",
    }
    if gaid:
        headers["App-Gaid"] = gaid

    return headers


def get_telegram_session_string() -> Optional[str]:
    """Retrieves Telethon string session from environment or local search paths."""
    env_sess = os.environ.get("TELEGRAM_STRING_SESSION")
    if env_sess and env_sess.strip():
        return env_sess.strip()

    search_paths = [
        Path("userbot.session.txt"),
        Path(r"c:\Users\chatu\Desktop\self meesho\userbot.session.txt"),
        Path(r"c:\Users\chatu\Desktop\Meesho - Arena\OTP-Gen\userbot.session.txt"),
    ]
    for p in search_paths:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return None


def refresh_account_via_telegram(
    account_data: Dict[str, Any],
    file_path: Optional[str] = None,
    timeout: int = 30
) -> Dict[str, Any]:
    """
    Refreshes the account's XO token using @Meeshojsoncheckerbot over Telethon.
    Updates file_path if provided.
    """
    if not account_data.get("ox"):
        raise ValueError("Cannot refresh session: 'ox' refresh token is missing from account JSON.")

    session_str = get_telegram_session_string()
    if not session_str:
        raise FileNotFoundError(
            "Telegram session not found. Set TELEGRAM_STRING_SESSION environment variable "
            "or place userbot.session.txt in workspace."
        )

    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise ImportError("Telethon is required for automatic token refresh. Run: pip install telethon")

    client = TelegramClient(StringSession(session_str), DEFAULT_TELEGRAM_API_ID, DEFAULT_TELEGRAM_API_HASH)

    print(f"[AuthManager] Refreshing token for user {account_data.get('user_id')} via @{BOT_USERNAME}...")
    with client:
        bot = client.get_entity(BOT_USERNAME)
        client.send_message(bot, "/start")
        time.sleep(2)

        # Look for 'JSON Refresh' button
        msgs = client.get_messages(bot, limit=5)
        button_clicked = False
        for m in msgs:
            if m.buttons:
                for r_idx, r in enumerate(m.buttons):
                    for c_idx, b in enumerate(r):
                        if "JSON Refresh" in b.text or "Refresh" in b.text:
                            m.click(r_idx, c_idx)
                            button_clicked = True
                            time.sleep(2)
                            break
                    if button_clicked:
                        break
            if button_clicked:
                break

        # Send account JSON payload
        sent_msg = client.send_message(bot, json.dumps(account_data))

        # Await reply
        start_time = time.time()
        refreshed_data = None

        while time.time() - start_time < timeout:
            time.sleep(2)
            replies = client.get_messages(bot, limit=5)
            for reply in replies:
                if reply.id <= sent_msg.id:
                    continue

                # Document reply
                if reply.file and ("refreshed" in (reply.file.name or "").lower() or (reply.file.name or "").endswith((".json", ".txt"))):
                    content_bytes = client.download_media(reply, bytes)
                    if content_bytes:
                        try:
                            refreshed_data = json.loads(content_bytes.decode("utf-8"))
                            break
                        except Exception:
                            pass

                # Text reply
                if reply.text and ('"xo"' in reply.text or "eyJ0eXBlIjoiY29tcG9zaXRlIn0=" in reply.text):
                    text = reply.text.strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                    try:
                        refreshed_data = json.loads(text.strip())
                        break
                    except Exception:
                        pass

            if refreshed_data:
                break

        if not refreshed_data:
            raise TimeoutError("Timed out waiting for @Meeshojsoncheckerbot to return refreshed JSON.")

        # Update in-memory data
        for k, v in refreshed_data.items():
            if v is not None:
                account_data[k] = v
        if "xo" in refreshed_data:
            account_data["xo"] = refreshed_data["xo"]
        if "ox" in refreshed_data:
            account_data["ox"] = refreshed_data["ox"]
        if "user_id" in refreshed_data:
            account_data["user_id"] = refreshed_data["user_id"]

        if file_path:
            save_account_json(account_data, file_path)
            print(f"[AuthManager] Updated account JSON on disk: {file_path}")

        print(f"[AuthManager] Successfully refreshed XO token for {account_data.get('mobile')}")
        return account_data

