"""
Proxy Manager and Egress Gateway for Meesho E2E Engine.
Provides:
- Global Proxy Configuration applied across all Meesho requests
- Proxybin Gateway Integration (https://proxybin.netlify.app/api/proxy)
- Region Toggle: Strictly Indian Egress ('in') vs Any Global Egress ('any'/None)
- Sticky Proxy Sessions per Account (maintains consistent egress node per user account)
- Robust Fallback Chain: 10x Proxybin -> 15x ProxyScrape Indian Pool -> Direct Local IP Backup ONLY when explicitly enabled
- Custom Proxy Support (HTTP/HTTPS/SOCKS5)
"""

import os
import time
import json
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Union
import requests
from requests.structures import CaseInsensitiveDict

logger = logging.getLogger("ProxyManager")

PROXYBIN_ENDPOINT = "https://proxybin.netlify.app/api/proxy"
PROXYBIN_IP_ENDPOINT = "https://proxybin.netlify.app/api/proxy/ip"

PROXY_FETCH_URLS = [
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&country=IN&proxy_format=protocolipport&format=text",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http,https&timeout=10000&country=IN&ssl=all&anonymity=all",
]

CACHE_TTL_SECONDS = 900
FAILURE_COOLDOWN_SECONDS = 600


class ProxyExhaustedError(RuntimeError):
    """Raised when proxy attempts are exhausted and direct backup is disabled."""
    pass


class ProxybinResponse(requests.Response):
    """Synthetic requests.Response wrapping Proxybin JSON payloads."""

    def __init__(self, pb_json: dict, original_url: str = ""):
        super().__init__()
        self.url = original_url
        self.status_code = pb_json.get("status", 200)
        self.reason = pb_json.get("statusText", "OK")

        data = pb_json.get("data")
        if data is not None and isinstance(data, (dict, list)):
            self._content = json.dumps(data).encode("utf-8")
            self._raw_data = data
        else:
            body = pb_json.get("body", "")
            self._content = str(body).encode("utf-8")
            self._raw_data = None

        self.headers = CaseInsensitiveDict(pb_json.get("headers", {}))
        self.proxy_country = pb_json.get("proxyCountry")
        self.direct_url = pb_json.get("direct_url")

    def json(self, **kwargs):
        if self._raw_data is not None:
            return self._raw_data
        return super().json(**kwargs)


@dataclass
class GlobalProxyConfig:
    """Thread-safe global proxy configuration across the entire engine."""
    mode: str = "proxybin_in"           # 'proxybin_in', 'proxybin_any', 'pool', 'custom', 'direct'
    custom_proxy: str = ""
    allow_direct_backup: bool = False   # Default local system IP is used ONLY when explicitly selected as backup
    max_proxybin_attempts: int = 10     # Proxybin up to 10 attempts
    max_pool_attempts: int = 15         # ProxyScrape pool up to 15 attempts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "custom_proxy": self.custom_proxy,
            "allow_direct_backup": self.allow_direct_backup,
            "max_proxybin_attempts": self.max_proxybin_attempts,
            "max_pool_attempts": self.max_pool_attempts
        }


class StickySessionManager:
    """
    Maintains session stickiness per account so that repeated requests for
    the same account do not hop across different egress nodes (e.g. US to CA).
    """

    def __init__(self):
        self._lock = threading.RLock()
        # account_key -> {'direct_url': str, 'pool_proxy': str, 'last_seen': float}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_sticky_direct_url(self, account_key: Optional[str]) -> Optional[str]:
        if not account_key:
            return None
        with self._lock:
            entry = self._sessions.get(str(account_key))
            return entry.get("direct_url") if entry else None

    def set_sticky_direct_url(self, account_key: Optional[str], direct_url: str):
        if not account_key or not direct_url:
            return
        with self._lock:
            k = str(account_key)
            if k not in self._sessions:
                self._sessions[k] = {}
            self._sessions[k]["direct_url"] = direct_url
            self._sessions[k]["last_seen"] = time.time()

    def get_sticky_pool_proxy(self, account_key: Optional[str]) -> Optional[str]:
        if not account_key:
            return None
        with self._lock:
            entry = self._sessions.get(str(account_key))
            return entry.get("pool_proxy") if entry else None

    def set_sticky_pool_proxy(self, account_key: Optional[str], proxy_url: str):
        if not account_key or not proxy_url:
            return
        with self._lock:
            k = str(account_key)
            if k not in self._sessions:
                self._sessions[k] = {}
            self._sessions[k]["pool_proxy"] = proxy_url
            self._sessions[k]["last_seen"] = time.time()

    def clear_sticky_session(self, account_key: Optional[str]):
        if not account_key:
            return
        with self._lock:
            self._sessions.pop(str(account_key), None)

    def clear(self):
        with self._lock:
            self._sessions.clear()


# Singletons
_CONFIG_LOCK = threading.RLock()
_GLOBAL_CONFIG = GlobalProxyConfig()
_STICKY_MANAGER = StickySessionManager()


def get_global_proxy_config() -> GlobalProxyConfig:
    with _CONFIG_LOCK:
        return GlobalProxyConfig(
            mode=_GLOBAL_CONFIG.mode,
            custom_proxy=_GLOBAL_CONFIG.custom_proxy,
            allow_direct_backup=_GLOBAL_CONFIG.allow_direct_backup,
            max_proxybin_attempts=_GLOBAL_CONFIG.max_proxybin_attempts,
            max_pool_attempts=_GLOBAL_CONFIG.max_pool_attempts
        )


def set_global_proxy_config(
    mode: Optional[str] = None,
    custom_proxy: Optional[str] = None,
    allow_direct_backup: Optional[bool] = None,
    max_proxybin_attempts: Optional[int] = None,
    max_pool_attempts: Optional[int] = None
) -> GlobalProxyConfig:
    with _CONFIG_LOCK:
        if mode is not None:
            _GLOBAL_CONFIG.mode = str(mode).strip().lower().replace("-", "_")
        if custom_proxy is not None:
            _GLOBAL_CONFIG.custom_proxy = str(custom_proxy).strip()
        if allow_direct_backup is not None:
            _GLOBAL_CONFIG.allow_direct_backup = bool(allow_direct_backup)
        if max_proxybin_attempts is not None:
            _GLOBAL_CONFIG.max_proxybin_attempts = max(1, int(max_proxybin_attempts))
        if max_pool_attempts is not None:
            _GLOBAL_CONFIG.max_pool_attempts = max(1, int(max_pool_attempts))
        return GlobalProxyConfig(
            mode=_GLOBAL_CONFIG.mode,
            custom_proxy=_GLOBAL_CONFIG.custom_proxy,
            allow_direct_backup=_GLOBAL_CONFIG.allow_direct_backup,
            max_proxybin_attempts=_GLOBAL_CONFIG.max_proxybin_attempts,
            max_pool_attempts=_GLOBAL_CONFIG.max_pool_attempts
        )


def get_sticky_manager() -> StickySessionManager:
    return _STICKY_MANAGER


class IndianProxyPool:
    """Thread-safe Indian proxy pool fetched from ProxyScrape with rotation and failover."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proxies: List[str] = []
        self._failed_proxies: Dict[str, float] = {}
        self._active_proxy: Optional[str] = None
        self._last_fetch_time: float = 0.0
        self._current_index: int = 0

    def fetch_proxyscrape_indian_proxies(self, force: bool = False) -> List[str]:
        now = time.time()
        with self._lock:
            if not force and self._proxies and (now - self._last_fetch_time < CACHE_TTL_SECONDS):
                return list(self._proxies)

        collected = []
        for url in PROXY_FETCH_URLS:
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200 and resp.text:
                    for line in resp.text.splitlines():
                        p = line.strip()
                        if not p or p.startswith("#") or "<" in p:
                            continue
                        if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks4://") or p.startswith("socks5://")):
                            p = f"http://{p}"
                        if p not in collected:
                            collected.append(p)
            except Exception as e:
                logger.debug(f"Failed fetching proxies from {url}: {e}")

        with self._lock:
            if collected:
                self._proxies = collected
                self._last_fetch_time = now
                self._current_index = 0
                logger.info(f"[ProxyManager] Loaded {len(self._proxies)} Indian proxies from ProxyScrape.")
            return list(self._proxies)

    def get_candidate_proxies(self) -> List[str]:
        now = time.time()
        with self._lock:
            self._failed_proxies = {
                p: ts for p, ts in self._failed_proxies.items()
                if (now - ts) < FAILURE_COOLDOWN_SECONDS
            }
            candidates = [p for p in self._proxies if p not in self._failed_proxies]
            if not candidates and self._proxies:
                self._failed_proxies.clear()
                candidates = list(self._proxies)
            return candidates

    def get_next_proxy(self, failed_proxy: Optional[str] = None, reason: str = "") -> Optional[str]:
        with self._lock:
            now = time.time()
            if failed_proxy:
                self._failed_proxies[failed_proxy] = now

        if not self._proxies:
            self.fetch_proxyscrape_indian_proxies()

        candidates = self.get_candidate_proxies()
        with self._lock:
            if not candidates:
                self._active_proxy = None
                return None

            self._current_index = (self._current_index + 1) % len(candidates)
            self._active_proxy = candidates[self._current_index]
            return self._active_proxy

    def get_active_proxy(self) -> Optional[str]:
        with self._lock:
            if self._active_proxy and self._active_proxy not in self._failed_proxies:
                return self._active_proxy
        return self.get_next_proxy()


_POOL = IndianProxyPool()


def request_via_proxybin(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Any] = None,
    params: Optional[Dict[str, Any]] = None,
    proxy_country: Optional[str] = "in",
    account_key: Optional[str] = None,
    timeout: int = 20,
    retries: int = 3
) -> requests.Response:
    """
    Routes an HTTP request through Proxybin rotating egress gateway (https://proxybin.netlify.app/api/proxy).
    Maintains sticky node per account when account_key is provided.
    proxy_country: 'in' for strictly Indian nodes, 'any'/None for global rotating nodes (US, EU, etc.).
    """
    final_url = url
    if params:
        from urllib.parse import urlencode
        sep = "&" if "?" in final_url else "?"
        final_url = f"{final_url}{sep}{urlencode(params)}"

    pb_payload: Dict[str, Any] = {
        "url": final_url,
        "method": method.upper(),
        "headers": headers or {},
        "retries": retries
    }
    if json_body is not None:
        pb_payload["body"] = json_body

    # Region filter: 'in' for strictly India, None for any global node
    if proxy_country and str(proxy_country).lower() not in ("any", "global", "none", "all", "*"):
        pb_payload["proxy_country"] = str(proxy_country).lower()

    # Sticky proxy per account
    if account_key:
        pb_payload["sticky"] = True
        direct_url = _STICKY_MANAGER.get_sticky_direct_url(account_key)
        if direct_url:
            pb_payload["direct_url"] = direct_url

    resp = requests.post(PROXYBIN_ENDPOINT, json=pb_payload, timeout=timeout)
    resp.raise_for_status()
    pb_data = resp.json()

    # Update sticky mapping if direct_url returned
    new_direct_url = pb_data.get("direct_url")
    if account_key and new_direct_url:
        _STICKY_MANAGER.set_sticky_direct_url(account_key, new_direct_url)

    return ProxybinResponse(pb_data, original_url=final_url)


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    account_key: Optional[str] = None,
    max_retries: Optional[int] = None,
    custom_proxy: Optional[str] = None,
    proxy_mode: Optional[str] = None,
    proxy_country: Optional[str] = None,
    allow_direct_backup: Optional[bool] = None,
    log_callback=None,
    **kwargs
) -> requests.Response:
    """
    Executes an HTTP request honoring:
    1. Global Proxy Configuration
    2. Sticky Proxy Per Account (session stickiness across calls for the same user)
    3. Multi-Tier Fallback Chain:
       - Stage 1: Try Proxybin up to 10 attempts
       - Stage 2: If Proxybin fails 10 times, try ProxyScrape Indian pool up to 15 attempts
       - Stage 3: Direct local IP ONLY when explicitly selected as backup
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)
        else:
            logger.info(msg)

    # Resolve active configuration (parameters override global defaults)
    g_cfg = get_global_proxy_config()

    mode = str(proxy_mode if proxy_mode is not None else g_cfg.mode).lower().strip().replace("-", "_")
    p_val = str(custom_proxy if custom_proxy is not None else g_cfg.custom_proxy).strip()
    backup_allowed = allow_direct_backup if allow_direct_backup is not None else g_cfg.allow_direct_backup
    max_pb_attempts = g_cfg.max_proxybin_attempts
    max_pool_attempts = g_cfg.max_pool_attempts

    # Case 1: Direct mode explicitly requested
    if mode in ("direct", "local", "none", "disabled") or p_val.lower() in ("direct", "none", "disabled"):
        session.proxies.clear()
        return session.request(method, url, **kwargs)

    # Case 2: Custom proxy URL given (http:// or socks5://)
    if mode == "custom" or (p_val and (p_val.startswith("http://") or p_val.startswith("https://") or p_val.startswith("socks5://"))):
        proxy_target = p_val or g_cfg.custom_proxy
        session.proxies = {"http": proxy_target, "https": proxy_target}
        for attempt in range(1, 4):
            try:
                resp = session.request(method, url, **kwargs)
                if resp.status_code in (502, 503, 504):
                    log(f"[ProxyManager] Custom proxy upstream HTTP {resp.status_code}. Retrying ({attempt}/3)...")
                    continue
                return resp
            except Exception as e:
                log(f"[ProxyManager] Custom proxy error on attempt {attempt}: {e}")
                time.sleep(0.5)

        if backup_allowed:
            log("[ProxyManager] Custom proxy failed. Local IP backup is enabled; falling back to direct connection...")
            session.proxies.clear()
            return session.request(method, url, **kwargs)
        raise ProxyExhaustedError("Custom proxy failed all attempts and local system IP backup is not enabled.")

    # Case 3: Proxybin Primary (Strictly India or Global Any)
    if mode.startswith("proxybin"):
        c_filter = "in"
        if "any" in mode or str(proxy_country).lower() in ("any", "global", "all"):
            c_filter = None

        log(f"[ProxyManager] Primary Egress: Proxybin (Region: {c_filter or 'global-any'}, Sticky: {bool(account_key)})...")
        pb_failed_count = 0

        for attempt in range(1, max_pb_attempts + 1):
            try:
                hdrs = kwargs.get("headers")
                if hdrs is None:
                    hdrs = dict(session.headers)

                pb_resp = request_via_proxybin(
                    method=method,
                    url=url,
                    headers=hdrs,
                    json_body=kwargs.get("json"),
                    params=kwargs.get("params"),
                    proxy_country=c_filter,
                    account_key=account_key,
                    timeout=kwargs.get("timeout", 20),
                    retries=1
                )
                if pb_resp.status_code < 500:
                    # Detect proxy-level gateway glitches: empty body on 400/403 or Cloudflare HTML error page
                    is_empty_err = (pb_resp.status_code >= 400 and not pb_resp.content)
                    is_cf_html = ("cloudflare" in pb_resp.headers.get("server", "").lower() and "json" not in pb_resp.headers.get("content-type", "").lower() and pb_resp.status_code >= 400)
                    if is_empty_err or is_cf_html:
                        log(f"[ProxyManager] Proxybin returned proxy-level error HTTP {pb_resp.status_code} (empty/HTML body). Clearing sticky session and retrying...")
                        _STICKY_MANAGER.clear_sticky_session(account_key)
                        pb_failed_count += 1
                        time.sleep(0.5)
                        continue
                    return pb_resp
                log(f"[ProxyManager] Proxybin returned HTTP {pb_resp.status_code} (attempt {attempt}/{max_pb_attempts}).")
                _STICKY_MANAGER.clear_sticky_session(account_key)
                pb_failed_count += 1
            except Exception as pb_err:
                log(f"[ProxyManager] Proxybin attempt {attempt}/{max_pb_attempts} failed: {pb_err}")
                _STICKY_MANAGER.clear_sticky_session(account_key)
                pb_failed_count += 1
                time.sleep(0.5)

        # Stage 2 Fallback: If Proxybin fails after 10 attempts, try ProxyScrape Indian pool up to 15 attempts
        log(f"[ProxyManager] Proxybin exhausted after {pb_failed_count} attempts. Falling back to ProxyScrape Indian pool (up to {max_pool_attempts} attempts)...")

        pool_proxy = _STICKY_MANAGER.get_sticky_pool_proxy(account_key) or _POOL.get_active_proxy()
        pool_failed_count = 0

        for attempt in range(1, max_pool_attempts + 1):
            if pool_proxy:
                session.proxies = {"http": pool_proxy, "https": pool_proxy}
            else:
                session.proxies.clear()

            try:
                resp = session.request(method, url, **kwargs)
                if resp.status_code < 500:
                    if account_key and pool_proxy:
                        _STICKY_MANAGER.set_sticky_pool_proxy(account_key, pool_proxy)
                    return resp
                log(f"[ProxyManager] Pool proxy {pool_proxy} returned HTTP {resp.status_code} (attempt {attempt}/{max_pool_attempts}).")
                pool_failed_count += 1
                pool_proxy = _POOL.get_next_proxy(failed_proxy=pool_proxy)
            except Exception as p_err:
                log(f"[ProxyManager] Pool proxy {pool_proxy} failed ({attempt}/{max_pool_attempts}): {p_err}")
                pool_failed_count += 1
                pool_proxy = _POOL.get_next_proxy(failed_proxy=pool_proxy, reason=str(p_err))
                time.sleep(0.5)

        # Stage 3 Fallback: Only if user explicitly enabled local system IP as backup
        if backup_allowed:
            log("[ProxyManager] Both Proxybin (10 attempts) and ProxyScrape pool (15 attempts) failed. Local IP backup is ENABLED; falling back to direct connection...")
            session.proxies.clear()
            return session.request(method, url, **kwargs)

        raise ProxyExhaustedError(
            f"Proxy failure: Exhausted {pb_failed_count} Proxybin attempts and {pool_failed_count} ProxyScrape pool attempts. "
            f"Local system IP backup is not enabled."
        )

    # Case 4: Public Pool Primary
    if mode == "pool":
        pool_proxy = _STICKY_MANAGER.get_sticky_pool_proxy(account_key) or _POOL.get_active_proxy()
        pool_failed = 0
        for attempt in range(1, max_pool_attempts + 1):
            if pool_proxy:
                session.proxies = {"http": pool_proxy, "https": pool_proxy}
            try:
                resp = session.request(method, url, **kwargs)
                if resp.status_code < 500:
                    if account_key and pool_proxy:
                        _STICKY_MANAGER.set_sticky_pool_proxy(account_key, pool_proxy)
                    return resp
                pool_failed += 1
                pool_proxy = _POOL.get_next_proxy(failed_proxy=pool_proxy)
            except Exception as e:
                pool_failed += 1
                pool_proxy = _POOL.get_next_proxy(failed_proxy=pool_proxy, reason=str(e))
                time.sleep(0.5)

        if backup_allowed:
            log("[ProxyManager] Proxy pool exhausted. Local IP backup is ENABLED; recovering via Direct Connection...")
            session.proxies.clear()
            return session.request(method, url, **kwargs)
        raise ProxyExhaustedError(f"Proxy pool exhausted after {pool_failed} attempts and local system IP backup is disabled.")

    # Default fallback: Direct if backup allowed, else error
    if backup_allowed:
        session.proxies.clear()
        return session.request(method, url, **kwargs)

    raise ProxyExhaustedError(f"Unsupported or unconfigured proxy mode: '{mode}' and local system IP backup is disabled.")
