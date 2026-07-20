from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from collections.abc import Iterator


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

_proxy_env_lock = threading.RLock()
_xhs_proxy_mode = "unknown"  # "direct", "proxy", or "unknown"


def _detect_xhs_proxy_mode() -> str:
    global _xhs_proxy_mode
    if _xhs_proxy_mode != "unknown":
        return _xhs_proxy_mode

    import requests
    test_url = "https://creator.xiaohongshu.com/api/media/v1/upload/creator/permit"
    
    # 1. Try Direct
    try:
        r = requests.get(test_url, proxies={'http': None, 'https': None}, timeout=3)
        _xhs_proxy_mode = "direct"
        return _xhs_proxy_mode
    except Exception:
        pass
        
    # 2. Try Proxy (using current env/system proxies)
    try:
        r = requests.get(test_url, timeout=3)
        _xhs_proxy_mode = "proxy"
        return _xhs_proxy_mode
    except Exception:
        pass
        
    # Fallback to direct if both failed (or network is down)
    _xhs_proxy_mode = "direct"
    return _xhs_proxy_mode


@contextmanager
def direct_xhs_request_env() -> Iterator[None]:
    """Run Spider_XHS SDK calls with or without proxies depending on auto-detection."""
    import requests
    mode = _detect_xhs_proxy_mode()
    
    with _proxy_env_lock:
        if mode == "direct":
            orig_request = requests.Session.request
            
            def patched_request(self, method, url, *args, **kwargs):
                if 'proxies' not in kwargs or kwargs['proxies'] is None:
                    kwargs['proxies'] = {'http': None, 'https': None}
                return orig_request(self, method, url, *args, **kwargs)
                
            requests.Session.request = patched_request
            
            original = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
            for key in PROXY_ENV_KEYS:
                os.environ.pop(key, None)
            try:
                yield
            finally:
                requests.Session.request = orig_request
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        else:
            try:
                yield
            except Exception as e:
                global _xhs_proxy_mode
                _xhs_proxy_mode = "unknown"
                raise e


