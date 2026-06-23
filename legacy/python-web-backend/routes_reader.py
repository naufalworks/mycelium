"""Reader API routes for the mycelium web backend."""

import json
import urllib.request
import urllib.parse
from fastapi import APIRouter

router = APIRouter(prefix="/api/reader", tags=["reader"])


@router.get("/fetch")
def api_reader_fetch(url: str = ""):
    """Fetch and extract clean content from a URL.
    Calls the Go reader tool (compiled into mycelium-proxy at :8443).
    Falls back to basic readability if Go endpoint unavailable.
    """
    if not url:
        return {"error": "url parameter required"}

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:8443/api/reader/fetch?url={urllib.parse.quote(url)}",
            headers={"User-Agent": "mycelium/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        pass

    try:
        import requests
        from readability import Document
        resp = requests.get(url, timeout=15, headers={"User-Agent": "mycelium/1.0"})
        doc = Document(resp.text)
        return {"title": doc.title(), "content": doc.summary(), "url": url}
    except Exception as e:
        return {"error": str(e)}
