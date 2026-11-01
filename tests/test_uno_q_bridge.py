"""Run with: python -B -m unittest discover -s tests -p test_uno_q_bridge.py -v"""

import importlib.util
import sys
import threading
import types
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

utils = types.ModuleType("arduino.app_utils")
utils.App = Mock()
utils.Bridge = Mock()
package = types.ModuleType("arduino")
package.app_utils = utils
path = Path(__file__).resolve().parents[1] / "python" / "main.py"
spec = importlib.util.spec_from_file_location("ghost_uno_q_helper", path)
helper = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"arduino": package, "arduino.app_utils": utils}):
    spec.loader.exec_module(helper)


class HelperTests(unittest.TestCase):
    def setUp(self):
        helper.Bridge.notify = Mock()
        self.url_patch = patch.object(helper, "SERVER_URL", "http://192.168.1.42:8000/haunt")
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)

    def response(self, body, content_type="text/plain", status=200):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = status
        response.headers = Message()
        response.headers["Content-Type"] = content_type
        response.read.return_value = body
        return response

    def read_message(self, body, **kwargs):
        response = self.response(body, **kwargs)
        with patch.object(helper.opener, "open", return_value=response) as open_request:
            text = helper.get_haunt()
        request = open_request.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "http://192.168.1.42:8000/haunt")
        self.assertEqual(request.get_header("Accept"), "text/plain")
        self.assertEqual(open_request.call_args.kwargs["timeout"], 3.0)
        response.read.assert_called_once_with(193)
        return text

    def test_trims_dormant_and_critique(self):
        self.assertEqual(self.read_message(b" \tdormant\r\n"), "dormant")
        self.assertEqual(self.read_message(b"  Your loops summon ancient bugs.\n"), "Your loops summon ancient bugs.")
        self.assertEqual(self.read_message(b"Dormant"), "Dormant")

    def test_eight_word_boundary(self):
        self.assertEqual(self.read_message(b"one two three four five six seven eight"), "one two three four five six seven eight")
        with self.assertRaises(ValueError):
            self.read_message(b"one two three four five six seven eight nine")

    def test_rejects_oversized_non_ascii_empty_and_controls(self):
        for body in (b"X" * 193, b"\xff", b"\r\n", b"bad\x00text", b"bad\ntext", b"bad\x1btext"):
            with self.subTest(body=body[:12]), self.assertRaises((ValueError, UnicodeError)):
                self.read_message(body)

    def test_rejects_wrong_content_type_or_status(self):
        for kwargs in ({"content_type": "application/json"}, {"status": 204}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.read_message(b"dormant", **kwargs)

    def test_configured_private_endpoint_only(self):
        self.assertEqual(helper.validate_url(helper.SERVER_URL), helper.SERVER_URL)
        urls = (
            "http://<PC_IP>:8000/haunt", "http://127.0.0.1:8000/haunt",
            "http://8.8.8.8:8000/haunt", "http://169.254.1.1:8000/haunt",
            "https://192.168.1.42:8000/haunt", "http://192.168.1.42:80/haunt",
            "http://192.168.1.42:8000/other", "http://user:pass@192.168.1.42:8000/haunt",
            "http://192.168.1.42:8000/haunt?target=other", "http://192.168.1.42:8000/haunt#other",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                helper.validate_url(url)

    def test_success_notification_preserves_request_id(self):
        with patch.object(helper, "get_haunt", return_value="dormant"):
            helper.fetch_haunt(42)
        helper.Bridge.notify.assert_called_once_with("ghost_haunt_result", 42, True, "dormant")

    def test_network_and_http_errors_are_separate_from_text(self):
        errors = (
            URLError("offline"), TimeoutError(),
            HTTPError(helper.SERVER_URL, 503, "Unavailable", {}, None),
            ValueError("Expected text/plain"),
        )
        for error in errors:
            helper.Bridge.notify = Mock()
            with self.subTest(error=type(error).__name__), patch.object(helper, "get_haunt", side_effect=error):
                helper.fetch_haunt(7)
            args = helper.Bridge.notify.call_args.args
            self.assertEqual(args[:3], ("ghost_haunt_result", 7, False))
            self.assertLessEqual(len(args[3]), helper.MAX_MESSAGE_BYTES)

    def test_lost_bridge_reply_does_not_raise(self):
        with patch.object(helper, "get_haunt", return_value="dormant"), patch.object(helper.Bridge, "notify", side_effect=ConnectionError()), patch("builtins.print") as log:
            helper.fetch_haunt(8)
        log.assert_called_once()

    def test_real_http_get_and_redirect_rejection(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/haunt")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Your code awakens ancient bugs.\n")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            # Production rejects loopback; bypass only this URL policy in this local transport test.
            with patch.object(helper, "validate_url", side_effect=lambda value: value):
                with patch.object(helper, "SERVER_URL", url + "/haunt"):
                    self.assertEqual(helper.get_haunt(), "Your code awakens ancient bugs.")
                with patch.object(helper, "SERVER_URL", url + "/redirect"), self.assertRaises(HTTPError) as caught:
                    helper.get_haunt()
                self.assertEqual(caught.exception.code, 302)
            self.assertEqual(paths, ["/haunt", "/redirect"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

