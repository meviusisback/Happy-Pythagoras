import os
import re
import random
import httpx
import unicodedata

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

try:
    from fake_useragent import UserAgent
    _HAS_FAKE_UA = True
except ImportError:
    _HAS_FAKE_UA = False

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]


def _pick_user_agent() -> str:
    if _HAS_FAKE_UA:
        try:
            return UserAgent().random
        except Exception:
            pass
    return random.choice(_FALLBACK_USER_AGENTS)


def _parse_sec_ch_ua(ua: str) -> str:
    m = re.search(r"(Chrome|Edg|Firefox|Safari)/(\d+)", ua)
    if m:
        brand = m.group(1)
        version = m.group(2)
        if brand == "Edg":
            brand = "Edge"
        elif brand == "Safari":
            brand = "Safari"
            if "Version" not in ua:
                version = "17"
        elif brand == "Firefox":
            pass
        return f'"{brand}";v="{version}", "Not_A Brand";v="99", "Chromium";v="{version}"'
    return '"Not_A Brand";v="99"'


def _parse_platform(ua: str) -> str:
    if "Windows" in ua:
        return '"Windows"'
    if "Mac" in ua or "macOS" in ua:
        return '"macOS"'
    if "Linux" in ua:
        return '"Linux"'
    if "Android" in ua:
        return '"Android"'
    if "iPhone" in ua or "iPad" in ua:
        return '"iOS"'
    return '"Unknown"'


def _referer_for_backend(backend: str) -> str:
    if backend == "bing":
        return "https://www.bing.com/"
    if backend == "ddg":
        return "https://duckduckgo.com/"
    return "https://www.google.com/"


def _browser_headers(backend: str = "google") -> dict:
    ua = _pick_user_agent()
    sec_ch_ua = _parse_sec_ch_ua(ua)
    platform = _parse_platform(ua)
    referer = _referer_for_backend(backend)

    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it,it-IT;q=0.9,en-US;q=0.5,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": sec_ch_ua,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": platform,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer,
    }


def make_async_client(timeout: int = 15) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def strip_diacritics(s: str) -> str:
    """Strip diacritics/accent marks from a string."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
