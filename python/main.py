"""UNO Q Linux helper: App Lab supplies arduino.app_utils; HTTP uses stdlib only."""

import ipaddress
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from arduino.app_utils import App, Bridge

# Configuration placeholders; Wi-Fi is set in App Lab/Linux, NOT in the sketch.
# WIFI_SSID = "<WIFI_SSID>"; WIFI_PASS = "<WIFI_PASS>"  (documentation only)
# Prefer `nmcli device wifi connect '<WIFI_SSID>' --ask`; never commit a password.
SERVER_URL = "http://<PC_IP>:8000/haunt"
HTTP_TIMEOUT_SECONDS = 3.0
MAX_MESSAGE_BYTES = 192


class NoRedirects(HTTPRedirectHandler):
    """Keep the trusted-LAN request on the configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_url(url: str) -> str:
    """Accept only a configured private LAN IPv4 /haunt endpoint."""
    parsed = urlsplit(url)
    address = ipaddress.ip_address(parsed.hostname or "")
    if (
        parsed.scheme != "http"
        or parsed.port != 8000
        or parsed.path != "/haunt"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise ValueError("Set SERVER_URL to http://<private LAN IPv4>:8000/haunt")
    return url


# Do not follow redirects or environment proxy settings for this LAN-only demo.
opener = build_opener(ProxyHandler({}), NoRedirects())


def get_haunt() -> str:
    """GET plain ASCII text, bounded in time and size; errors propagate to RPC handler."""
    request = Request(validate_url(SERVER_URL), headers={"Accept": "text/plain"}, method="GET")
    with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError("Expected HTTP 200")
        if response.headers.get_content_type() != "text/plain":
            raise ValueError("Expected text/plain")
        body = response.read(MAX_MESSAGE_BYTES + 1)
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Response exceeds 192 bytes")
    message = body.decode("ascii").strip()
    if not message:
        raise ValueError("Empty response")
    if any(ord(char) < 32 or ord(char) > 126 for char in message):
        raise ValueError("Expected printable ASCII text")
    if message != "dormant" and len(message.split()) > 8:
        raise ValueError("Critique must contain at most 8 words")
    return message


def fetch_haunt(request_id: int) -> None:
    """Handle a sketch notification and return a separate result notification."""
    try:
        message = get_haunt()
        ok = True
    except HTTPError as exc:
        ok, message = False, f"HTTP error {exc.code}"
    except (URLError, OSError, HTTPException, UnicodeError, ValueError) as exc:
        ok = False
        # No response body/URL/credentials in error logs; validation errors are local.
        message = str(exc) if isinstance(exc, ValueError) else f"Network/response error: {type(exc).__name__}"
        message = message[:MAX_MESSAGE_BYTES]
    try:
        # Notifications are allowed from a provided handler; nested Bridge.call is not.
        Bridge.notify("ghost_haunt_result", request_id, ok, message)
    except Exception as exc:
        print(f"Could not deliver haunt result: {type(exc).__name__}", flush=True)
        # The sketch's millis() timeout keeps it polling if this notification is lost.


if __name__ == "__main__":
    Bridge.provide("ghost_fetch_haunt", fetch_haunt)
    App.run()

