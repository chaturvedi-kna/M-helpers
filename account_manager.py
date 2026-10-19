"""
Account Manager for Meesho E2E Engine.
Provides schema validation, token metadata inspection, atomic persistence,
and decoupled refresh providers for safe account data retrieval.
"""

import base64
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
import requests
from auth import load_account_json

logger = logging.getLogger("AccountManager")


@dataclass
class TokenMetadata:
    """Decoded metadata from Meesho composite XO token."""
    user_id: Optional[str]
    instance_id: Optional[str]
    issued_at: Optional[float]
    expires_at: Optional[float]
    is_expired: bool
    remaining_seconds: float
    raw_jwt: Optional[str] = None
    internal_xo: Optional[str] = None


class AccountValidationError(ValueError):
    """Raised when an account record fails schema validation."""
    pass


class TokenRefreshError(RuntimeError):
    """Raised when token refresh fails."""
    pass


def validate_account_schema(data: Any) -> Tuple[bool, List[str]]:
    """
    Validates whether data satisfies the expected Meesho account JSON schema.
    Returns (is_valid, list_of_errors).
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["Account data must be a JSON object (dict)."]

    # user_id check
    user_id = data.get("user_id")
    if user_id is None:
        errors.append("Missing required field: 'user_id'")
    elif not (isinstance(user_id, (int, str)) and str(user_id).strip().isdigit()):
        errors.append(f"Field 'user_id' must be a numeric integer or string, got {type(user_id).__name__}")

    # mobile check
    mobile = data.get("mobile")
    phone = data.get("phone")
    if not mobile and not phone:
        errors.append("At least one of 'mobile' or 'phone' must be provided.")
    else:
        num_str = str(mobile or phone).replace("+91", "").strip()
        if not (num_str.isdigit() and len(num_str) == 10):
            errors.append(f"Invalid phone/mobile format: '{mobile or phone}'. Expected 10 digits.")

    # xo token check
    xo = data.get("xo")
    if not xo:
        errors.append("Missing required field: 'xo' (composite access token)")
    elif not isinstance(xo, str) or "." not in xo:
        errors.append("Invalid 'xo' format: expected base64 composite token separated by '.'")

    # ox refresh token check
    ox = data.get("ox")
    if ox is not None and not isinstance(ox, str):
        errors.append(f"Field 'ox' must be a string, got {type(ox).__name__}")

    # identity sub-dictionary check if present
    if "identity" in data:
        if not isinstance(data["identity"], dict):
            errors.append("Field 'identity' must be an object/dict if present.")

    return len(errors) == 0, errors


def parse_xo_token(xo_string: str) -> TokenMetadata:
    """
    Parses composite Meesho XO token into TokenMetadata.
    Composite format: <base64_header>.<base64_payload_json>
    where payload JSON contains {"jwt": "<jwt>", "xo": "<hex>"}
    """
    if not xo_string or not isinstance(xo_string, str) or "." not in xo_string:
        raise ValueError("Invalid XO token structure.")

    parts = xo_string.split(".")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)

    try:
        outer_json = json.loads(base64.b64decode(padded).decode("utf-8", errors="replace"))
    except Exception as e:
        raise ValueError(f"Could not decode composite XO payload: {e}")

    jwt_str = outer_json.get("jwt") or ""
    internal_xo = outer_json.get("xo")

    user_id = None
    instance_id = None
    issued_at = None
    expires_at = None

    if jwt_str and "." in jwt_str:
        jwt_parts = jwt_str.split(".")
        if len(jwt_parts) >= 2:
            jwt_payload_b64 = jwt_parts[1] + "=" * (-len(jwt_parts[1]) % 4)
            try:
                jwt_claims = json.loads(base64.b64decode(jwt_payload_b64).decode("utf-8", errors="replace"))
                issued_at = float(jwt_claims.get("iat")) if "iat" in jwt_claims else None
                expires_at = float(jwt_claims.get("exp")) if "exp" in jwt_claims else None
                user_id = str(jwt_claims.get("https://meesho.com/user_id") or "")
                instance_id = str(jwt_claims.get("https://meesho.com/instance_id") or "")
            except Exception as e:
                logger.debug(f"Failed parsing inner JWT claims: {e}")

    now = time.time()
    if expires_at is not None:
        rem = expires_at - now
        is_exp = rem <= 0
    else:
        rem = 0.0
        is_exp = False

    return TokenMetadata(
        user_id=user_id,
        instance_id=instance_id,
        issued_at=issued_at,
        expires_at=expires_at,
        is_expired=is_exp,
        remaining_seconds=rem,
        raw_jwt=jwt_str,
        internal_xo=internal_xo
    )


def is_token_expired(account_data: Dict[str, Any], buffer_seconds: int = 120) -> bool:
    """
    Determines whether an account's XO token is expired or will expire within buffer_seconds.
    """
    xo = account_data.get("xo")
    if not xo:
        return True
    try:
        meta = parse_xo_token(xo)
        if meta.expires_at is None:
            return False
        return meta.remaining_seconds <= buffer_seconds
    except Exception:
        # If token cannot be parsed, treat as needing refresh
        return True


def atomic_save_account_json(account_data: Dict[str, Any], file_path: Union[str, Path], create_backup: bool = True):
    """
    Safely writes account data to disk using an atomic replace operation to prevent file corruption.
    Validates schema before writing and creates a .bak backup file if requested.
    """
    is_valid, errors = validate_account_schema(account_data)
    if not is_valid:
        raise AccountValidationError(f"Cannot save invalid account data: {'; '.join(errors)}")

    dest = Path(file_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Optional backup
    if create_backup and dest.is_file():
        bak_path = dest.with_suffix(dest.suffix + ".bak")
        try:
            shutil.copy2(dest, bak_path)
        except Exception as e:
            logger.warning(f"Could not create backup file {bak_path}: {e}")

    # Write to temp file in same directory
    temp_file = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{int(time.time()*1000)}")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(account_data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace
        os.replace(temp_file, dest)
    except Exception as e:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        raise IOError(f"Atomic write failed for {dest}: {e}")


class BaseTokenRefreshProvider:
    """Abstract interface for token refresh mechanisms."""
    def refresh(self, account_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class DirectApiTokenRefreshProvider(BaseTokenRefreshProvider):
    """
    Direct official API token refresh provider using Meesho's /api/2.0/xo endpoint.
    Refreshes expired or near-expired composite XO tokens directly without requiring
    Telegram userbots, web browsers, or OTP prompts.
    """
    DEFAULT_ENDPOINT = "https://prod.meeshoapi.com/api/2.0/xo"

    def __init__(self, timeout: int = 15, api_host: Optional[str] = None):
        self.timeout = timeout
        self.api_host = (api_host or "https://prod.meeshoapi.com").rstrip("/")

    def refresh(self, account_data: Dict[str, Any]) -> Dict[str, Any]:
        ox = account_data.get("ox")
        if not ox:
            raise TokenRefreshError("Cannot refresh: missing 'ox' refresh token.")

        user_id = account_data.get("user_id")
        if not user_id:
            xo = account_data.get("xo")
            if xo:
                try:
                    meta = parse_xo_token(xo)
                    if meta.user_id and meta.user_id.isdigit():
                        user_id = int(meta.user_id)
                except Exception:
                    pass

        if not user_id:
            raise TokenRefreshError("Cannot refresh: missing 'user_id' in account data.")

        phone = account_data.get("phone") or (f"+91{account_data['mobile']}" if account_data.get("mobile") else "")
        u_token = base64.b64encode(phone.encode()).decode() if phone else ""
        identity = account_data.get("identity") or {}

        headers = {
            "Authorization": account_data.get("authorization") or "32c4d8137cn9eb493a1921f203173080",
            "App-Version": "29.5",
            "App-Version-Code": "868",
            "Instance-Id": str(account_data.get("instance_id") or identity.get("instance_id") or "unknown"),
            "Country-Iso": "in",
            "Application-Id": "com.meesho.supply",
            "App-Session-Id": str(identity.get("app_session_id") or "1a047084893d4f8fb23f1adebb000cf8"),
            "App-Sdk-Version": "34",
            "App-Client-Id": "android",
            "Shield-Session-Id": str(identity.get("shield_session_id") or "1a047084893d4f8fb23f1adebb000cf8"),
            "Xo": str(account_data.get("xo", "")),
            "App-User-Id": str(user_id),
            "U-Token": u_token,
            "Meesho-User-Context": "logged_in",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.9.0"
        }

        url = f"{self.api_host}/api/2.0/xo"
        payload = {"ox": ox, "user_id": int(user_id)}

        try:
            logger.info(f"Attempting Direct API token refresh for user {user_id} via {url}...")
            import proxy_manager
            sess = requests.Session()
            sess.headers.update(headers)
            resp = proxy_manager.request_with_retry(
                session=sess,
                method="POST",
                url=url,
                account_key=str(user_id),
                json=payload,
                timeout=self.timeout
            )
        except Exception as e:
            raise TokenRefreshError(f"Direct API refresh network error: {e}")

        if resp.status_code != 200:
            err_detail = resp.text.strip()[:200]
            if resp.status_code == 400:
                raise TokenRefreshError(f"Direct API refresh returned HTTP 400 (Session/ox refresh token invalid or revoked on Meesho): {err_detail or '<empty body>'}")
            raise TokenRefreshError(f"Direct API refresh returned HTTP {resp.status_code}: {err_detail}")

        try:
            data = resp.json()
        except Exception as e:
            raise TokenRefreshError(f"Direct API refresh response was not valid JSON: {e}")

        xoox = data.get("xoox") or {}
        new_xo = xoox.get("xo")
        new_ox = xoox.get("ox")

        if not new_xo:
            raise TokenRefreshError(f"Direct API refresh response missing 'xo' field: {resp.text[:200]}")

        updated = dict(account_data)
        updated["xo"] = new_xo
        if new_ox:
            updated["ox"] = new_ox

        logger.info(f"Direct API token refresh succeeded for user {user_id}!")
        return updated


class TelegramTokenRefreshProvider(BaseTokenRefreshProvider):
    """Refreshes tokens via @Meeshojsoncheckerbot using Telethon."""

    def __init__(
        self,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        session_str: Optional[str] = None,
        bot_username: Optional[str] = None,
        timeout: int = 35
    ):
        from config import DEFAULT_TELEGRAM_API_ID, DEFAULT_TELEGRAM_API_HASH, BOT_USERNAME
        from auth import get_telegram_session_string

        self.api_id = api_id or DEFAULT_TELEGRAM_API_ID
        self.api_hash = api_hash or DEFAULT_TELEGRAM_API_HASH
        self.session_str = session_str or get_telegram_session_string()
        self.bot_username = bot_username or BOT_USERNAME
        self.timeout = timeout

    def refresh(self, account_data: Dict[str, Any]) -> Dict[str, Any]:
        if not account_data.get("ox"):
            raise TokenRefreshError("Cannot refresh: missing 'ox' refresh token.")
        if not self.session_str:
            raise TokenRefreshError("Telegram session string not found. Ensure userbot.session.txt exists.")

        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
        except ImportError:
            raise TokenRefreshError("Telethon library not installed.")

        client = TelegramClient(StringSession(self.session_str), self.api_id, self.api_hash)
        logger.info(f"Connecting to Telegram @{self.bot_username} to refresh token...")

        with client:
            bot = client.get_entity(self.bot_username)
            client.send_message(bot, "/start")
            time.sleep(1.5)

            # Click refresh button if present
            msgs = client.get_messages(bot, limit=5)
            for m in msgs:
                if m.buttons:
                    for r_idx, r in enumerate(m.buttons):
                        for c_idx, b in enumerate(r):
                            if "Refresh" in b.text:
                                m.click(r_idx, c_idx)
                                time.sleep(1.5)
                                break

            # Send JSON payload
            sent = client.send_message(bot, json.dumps(account_data))

            start_t = time.time()
            refreshed_payload = None

            while time.time() - start_t < self.timeout:
                time.sleep(2)
                replies = client.get_messages(bot, limit=5)
                for reply in replies:
                    if reply.id <= sent.id:
                        continue

                    # Document reply
                    if reply.file and ("refreshed" in (reply.file.name or "").lower() or (reply.file.name or "").endswith((".json", ".txt"))):
                        media_bytes = client.download_media(reply, bytes)
                        if media_bytes:
                            try:
                                refreshed_payload = json.loads(media_bytes.decode("utf-8"))
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
                            refreshed_payload = json.loads(text.strip())
                            break
                        except Exception:
                            pass

                if refreshed_payload:
                    break

            if not refreshed_payload:
                raise TokenRefreshError("Timed out waiting for bot response.")

            # Merge updated fields safely without corrupting original fields
            updated = dict(account_data)
            if "xo" in refreshed_payload:
                updated["xo"] = refreshed_payload["xo"]
            if "ox" in refreshed_payload:
                updated["ox"] = refreshed_payload["ox"]
            if "user_id" in refreshed_payload:
                updated["user_id"] = refreshed_payload["user_id"]

            return updated


class MockTokenRefreshProvider(BaseTokenRefreshProvider):
    """Deterministic token refresh provider for testing and offline verification."""

    def __init__(self, ttl_seconds: int = 172800):
        self.ttl_seconds = ttl_seconds

    def refresh(self, account_data: Dict[str, Any]) -> Dict[str, Any]:
        user_id = account_data.get("user_id", 492252136)
        instance_id = account_data.get("instance_id", "mock_instance_id")
        now = int(time.time())
        exp = now + self.ttl_seconds

        claims = {
            "exp": exp,
            "iat": now,
            "https://meesho.com/instance_id": str(instance_id),
            "https://meesho.com/user_id": str(user_id)
        }
        claims_b64 = base64.b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        mock_jwt = f"eyJhbGciOiJIUzI1NiJ9.{claims_b64}.mock_signature"

        outer = {"jwt": mock_jwt, "xo": "mock_hex_xo_token_refreshed"}
        outer_b64 = base64.b64encode(json.dumps(outer).encode()).decode().rstrip("=")

        header = base64.b64encode(json.dumps({"type": "composite"}).encode()).decode().rstrip("=")
        new_xo = f"{header}.{outer_b64}"

        updated = dict(account_data)
        updated["xo"] = new_xo
        if "ox" in updated:
            updated["ox"] = f"mock_ox_refresh_{now}"

        return updated


def get_default_refresh_provider() -> BaseTokenRefreshProvider:
    """
    Returns the primary token refresh provider.
    Prefers DirectApiTokenRefreshProvider (official Meesho /api/2.0/xo endpoint)
    as it executes synchronously in milliseconds without requiring Telegram or OTP.
    """
    return DirectApiTokenRefreshProvider()


def refresh_accounts_from_source(
    source: Any,
    output_dir: Optional[Union[str, Path]] = None,
    format_type: str = "combined_json",
    provider: Optional[BaseTokenRefreshProvider] = None
) -> Dict[str, Any]:
    """
    Refreshes accounts from any input format (single JSON, folder, merged array, JSONL)
    and outputs results as individual 1-by-1 .txt files in category subfolders
    or a combined JSON file.
    """
    from data_io import load_accounts_from_source, export_categorized_results

    accounts = load_accounts_from_source(source)
    refresh_prov = provider or DirectApiTokenRefreshProvider()
    refreshed_list = []
    failed_list = []

    for acc in accounts:
        user_id = str(acc.get("user_id") or acc.get("mobile") or "unknown")
        try:
            refreshed = refresh_prov.refresh(acc)
            refreshed_list.append(refreshed)
        except Exception as e:
            failed_list.append({
                "account_id": user_id,
                "source_reference": acc.get("source_reference"),
                "error": str(e),
                "raw_record": acc
            })

    results = {
        "refreshed": refreshed_list,
        "failed": failed_list
    }

    if output_dir:
        export_categorized_results(results, output_dir=output_dir, format_type=format_type)

    return {
        "total": len(accounts),
        "refreshed_count": len(refreshed_list),
        "failed_count": len(failed_list),
        "refreshed": refreshed_list,
        "failed": failed_list
    }


