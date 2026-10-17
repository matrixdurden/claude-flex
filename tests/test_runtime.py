import hashlib
import io
import socket
import tarfile
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from claude_flex.gateway import Gateway, install
from claude_flex.providers import request_json
from claude_flex.store import FlexError, Store, write_json


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "private")
        self.store.init()

    def archive(self):
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode="w:gz") as archive:
            for filename in ("cli-proxy-api", "../../unwanted"):
                info = tarfile.TarInfo(filename)
                payload = b"synthetic executable"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return content.getvalue()

    def installer_context(self, content, *, digest=None):
        asset = {"name": "CLIProxyAPI_8.0.32_linux_amd64.tar.gz", "digest": "sha256:" + (digest or hashlib.sha256(content).hexdigest()), "browser_download_url": "https://example.invalid/archive.tar.gz"}
        return (
            patch("claude_flex.gateway.platform.system", return_value="Linux"),
            patch("claude_flex.gateway.platform.machine", return_value="x86_64"),
            patch("claude_flex.gateway.executable_name", return_value="cli-proxy-api"),
            patch("claude_flex.gateway.request_json", return_value={"assets": [asset]}),
            patch("claude_flex.gateway.opener"),
        )

    def test_installer_verifies_digest_and_extracts_only_named_binary(self):
        content = self.archive()
        a, b, c, d, e = self.installer_context(content)
        with a, b, c, d, e as opened:
            opened.return_value.open.return_value = io.BytesIO(content)
            binary = install(self.store)
        self.assertEqual(binary.read_bytes(), b"synthetic executable")
        self.assertFalse((Path(self.temp.name) / "unwanted").exists())
        self.assertEqual(self.store.load()["gateway"]["binary"], str(binary))

    def test_installer_checksum_failure_never_installs(self):
        content = self.archive()
        a, b, c, d, e = self.installer_context(content, digest="0" * 64)
        with a, b, c, d, e as opened:
            opened.return_value.open.return_value = io.BytesIO(content)
            with self.assertRaises(FlexError):
                install(self.store)
        self.assertEqual(self.store.load()["gateway"]["binary"], "")

    def test_public_release_download_follows_redirect_without_credentials(self):
        content = self.archive()
        auth_headers = []

        class AssetServer(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                auth_headers.append(self.headers.get("Authorization"))
                if self.path == "/release":
                    self.send_response(302)
                    self.send_header("Location", "/asset")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

        server = ThreadingHTTPServer(("127.0.0.1", 0), AssetServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        asset = {"name": "CLIProxyAPI_8.0.32_linux_amd64.tar.gz", "digest": "sha256:" + hashlib.sha256(content).hexdigest(), "browser_download_url": f"http://127.0.0.1:{server.server_port}/release"}
        with patch("claude_flex.gateway.platform.system", return_value="Linux"), patch("claude_flex.gateway.platform.machine", return_value="x86_64"), patch("claude_flex.gateway.executable_name", return_value="cli-proxy-api"), patch("claude_flex.gateway.request_json", return_value={"assets": [asset]}):
            binary = install(self.store)
        self.assertEqual(binary.read_bytes(), b"synthetic executable")
        self.assertEqual(auth_headers, [None, None])

    def test_stale_pid_never_signals_an_unrelated_process(self):
        gateway = Gateway(self.store)
        write_json(gateway.record_path, {"pid": 99999})
        with patch("claude_flex.gateway.process_command", return_value="unrelated-server"), patch("claude_flex.gateway.os.kill") as kill:
            gateway.stop()
        kill.assert_not_called()

    def test_model_readiness_waits_for_async_credential_loading(self):
        gateway = Gateway(self.store)
        with patch.object(gateway, "models", side_effect=[[], [{"id": "wanted"}]]), patch("claude_flex.gateway.time.sleep"):
            gateway.wait_for_model("wanted")

    def test_occupied_port_does_not_spawn_a_second_gateway(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            self.store.mutate(lambda config: config["gateway"].update(port=sock.getsockname()[1]))
            with patch("claude_flex.gateway.subprocess.Popen") as spawn, self.assertRaises(FlexError):
                Gateway(self.store).start()
            spawn.assert_not_called()

    def test_upstream_errors_do_not_print_echoed_keys_or_body(self):
        error = urllib.error.HTTPError("http://localhost", 401, "no", {}, io.BytesIO(b"secret-key-from-upstream"))
        with patch("claude_flex.providers.opener") as opened:
            opened.return_value.open.side_effect = error
            with self.assertRaises(FlexError) as caught:
                request_json("http://localhost/v1/models", headers={"Authorization": "Bearer secret"})
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
