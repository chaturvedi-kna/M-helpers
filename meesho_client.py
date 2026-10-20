"""
Core Meesho API Client with Proxy Rotation and Auto-Token Refresh.
"""

import logging
import requests
from typing import Dict, Any, Optional

from config import DEFAULT_API_HOST, DEFAULT_TIMEOUT
from auth import build_meesho_headers, refresh_account_via_telegram
import proxy_manager

logger = logging.getLogger("MeeshoClient")


class MeeshoClient:
    """Session-wrapped client for interacting with Meesho API endpoints."""

    def __init__(
        self,
        account_data: Dict[str, Any],
        account_path: Optional[str] = None,
        custom_proxy: Optional[str] = None,
        auto_refresh: bool = True,
        api_host: str = DEFAULT_API_HOST,
        log_callback=None,
        refresh_provider=None
    ):
        self.account_data = account_data
        self.account_path = account_path
        self.custom_proxy = custom_proxy
        self.auto_refresh = auto_refresh
        self.api_host = api_host.rstrip("/")
        self.log_callback = log_callback
        self.refresh_provider = refresh_provider
        self.cart_session: Optional[str] = None

        self.session = requests.Session()
        self.update_headers()


    def log(self, msg: str):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg, flush=True)

    def update_headers(self):
        """Refreshes the internal session headers using current account_data."""
        headers = build_meesho_headers(self.account_data)
        self.session.headers.clear()
        self.session.headers.update(headers)

    def api_call(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        allow_refresh_retry: bool = True,
        timeout: int = DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        """
        Executes an HTTP request to Meesho API.
        If a 401 Unauthorized with Proxy Code 1014 is encountered, automatically
        refreshes the XO token and retries once.
        """
        url = f"{self.api_host}{path}"
        acc_key = str(self.account_data.get("user_id") or self.account_data.get("mobile") or "")
        r = proxy_manager.request_with_retry(
            session=self.session,
            method=method,
            url=url,
            account_key=acc_key or None,
            json=payload,
            params=params,
            timeout=timeout,
            custom_proxy=self.custom_proxy,
            log_callback=self.log
        )

        # Check for rate limiting
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "5")
            self.log(f"[MeeshoClient] HTTP 429 Rate Limited on {path}. Retry-After: {retry_after}s")
            raise ConnectionError(f"HTTP 429 Too Many Requests on {path}. Rate limit reached.")

        # Check for token expiration / auth failure
        if r.status_code == 401 and allow_refresh_retry and self.auto_refresh:
            proxy_failure_code = r.headers.get("x-proxy-auth-failure-code", "")
            if proxy_failure_code == "1007":
                self.log(f"[MeeshoClient] HTTP 401 (Code 1007: Session Permanently Revoked). Re-login required.")
                raise PermissionError("HTTP 401: Account session revoked (Proxy Code 1007). Fresh login required.")

            self.log(f"[MeeshoClient] HTTP 401 received (Proxy failure code: {proxy_failure_code or '1014'}). Triggering auto-refresh...")
            try:
                self.refresh_session()
                self.log("[MeeshoClient] XO token refreshed successfully. Retrying request...")
                return self.api_call(method, path, payload, params, allow_refresh_retry=False, timeout=timeout)
            except Exception as e:
                self.log(f"[MeeshoClient] Auto-refresh failed: {e}")
                raise PermissionError(f"HTTP 401: Token expired and auto-refresh failed ({e})")

        if r.status_code == 401:
            p_code = r.headers.get("x-proxy-auth-failure-code", "")
            raise PermissionError(f"HTTP 401 on {path} (Proxy Code: {p_code}): {r.text[:300]}")

        if r.status_code >= 400:
            err_desc = ""
            try:
                err_data = r.json()
                err_desc = err_data.get("description") or err_data.get("message") or err_data.get("error") or r.text[:300]
            except Exception:
                err_desc = r.text[:300] if r.content else f"Status {r.status_code}"
            self.log(f"[MeeshoClient] HTTP {r.status_code} on {path}: {err_desc}")
            raise requests.exceptions.HTTPError(f"HTTP {r.status_code} ({err_desc}) on {path}", response=r)

        return r.json() if r.content else {}

    def refresh_session(self) -> Dict[str, Any]:
        """Refreshes the active account session using configured provider, direct Meesho API, or Telegram fallback."""
        refreshed = None
        if self.refresh_provider:
            refreshed = self.refresh_provider.refresh(self.account_data)
        else:
            # 1. Prefer Direct API refresh (/api/2.0/xo)
            try:
                from account_manager import DirectApiTokenRefreshProvider
                direct_provider = DirectApiTokenRefreshProvider()
                refreshed = direct_provider.refresh(self.account_data)
                self.log("[MeeshoClient] Session refreshed via direct Meesho API (/api/2.0/xo).")
            except Exception as e:
                self.log(f"[MeeshoClient] Direct API refresh encountered ({e}), attempting Telegram bot fallback...")
                refreshed = refresh_account_via_telegram(self.account_data, self.account_path)

        self.account_data = refreshed
        self.update_headers()

        if self.account_path:
            from account_manager import atomic_save_account_json
            atomic_save_account_json(self.account_data, self.account_path)
            self.log(f"[MeeshoClient] Saved refreshed account to {self.account_path}")

        return self.account_data

    def get_profile(self) -> Dict[str, Any]:
        """Validates current session against /api/1.0/user-profile."""
        return self.api_call("GET", "/api/1.0/user-profile")


