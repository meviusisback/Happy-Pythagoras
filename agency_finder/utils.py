import os
import httpx
import unicodedata

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def make_async_client(timeout: int = 15) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def strip_diacritics(s: str) -> str:
    """Strip diacritics/accent marks from a string."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
