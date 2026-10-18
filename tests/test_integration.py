"""Real gateway, local synthetic upstream; never calls OpenAI/Anthropic."""

import argparse
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from claude_flex.cli import add_model, check_model, main
from claude_flex.compiler import model_alias, render
from claude_flex.gateway import Gateway
from claude_flex.providers import add_key, add_provider, request_json
from claude_flex.store import Store
from tests.helpers import free_port, token

BACKEND = os.environ.get("CLAUDE_FLEX_TEST_BACKEND")


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_json({"data": [{"id": "upstream-a"}, {"id": "upstream-b"}]})

    def send_json(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "")
        self.server.requests.append({"key": key, "body": body})
        if key == "Bearer synthetic-reject":
            self.send_json({"error": {"message": "synthetic quota exhausted", "type": "rate_limit_error"}}, 429)
            return
        self.server.success_keys.add(key)
        model = body["model"]
        used_tool = any(message.get("role") == "tool" for message in body.get("messages", []))
        tool = bool(body.get("tools")) and not used_tool
        if tool:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-probe", "type": "function", "function": {"name": "flex_probe", "arguments": '{"value":323}'}}]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "FLEX_OK:" + model}
            finish = "stop"
        if not body.get("stream"):
            self.send_json({"id": "chatcmpl-synthetic", "object": "chat.completion", "created": 1, "model": model, "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        delta = {"role": "assistant"}
        if tool:
            delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
        else:
            delta["content"] = message["content"]
        chunk = {"id": "chatcmpl-synthetic", "object": "chat.completion.chunk", "created": 1, "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
        chunk["usage"] = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
        self.wfile.flush()


@unittest.skipUnless(BACKEND, "Set CLAUDE_FLEX_TEST_BACKEND for real-backend integration tests")
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.server.requests = []
        self.server.success_keys = set()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.store = Store(Path(self.temp.name) / "state")
        self.store.init(port=free_port())
        self.store.mutate(lambda config: config["gateway"].update(binary=str(Path(BACKEND).resolve())))
        self.store.mutate(lambda config: config["routing"].update(sticky=False))
        add_provider(self.store, "fake", kind="openai", url=f"http://127.0.0.1:{self.server.server_port}/v1")
        add_key(self.store, "fake", "reject", value="synthetic-reject")
        add_key(self.store, "fake", "accept", value="synthetic-accept")
        add_model(self.store, "one", "fake", "upstream-a")
        add_model(self.store, "two", "fake", "upstream-b")
        self.gateway = Gateway(self.store)
        self.gateway.start()
        self.addCleanup(self.gateway.stop)
        self.headers = {"Authorization": "Bearer " + self.store.secrets()["gateway_key"], "anthropic-version": "2023-06-01"}

    def call(self, profile="one", text="probe"):
        return request_json(self.store.url() + "/v1/messages", headers=self.headers, body={"model": model_alias(profile), "max_tokens": 128, "messages": [{"role": "user", "content": text}]})

    def test_anthropic_to_openai_and_account_key_failover(self):
        for number in range(4):
            response = self.call(text=f"independent-probe-{number}")
            self.assertIn("FLEX_OK:upstream-a", str(response["content"]))
        self.assertIn("Bearer synthetic-reject", {item["key"] for item in self.server.requests})
        self.assertIn("Bearer synthetic-accept", self.server.success_keys)

    def test_complete_tool_call_and_result_round_trip(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            check_model(self.store, argparse.Namespace(profile="one", tools=True))
        self.assertIn("round trip passed", output.getvalue())
        forwarded = [item["body"] for item in self.server.requests]
        self.assertTrue(any(message.get("role") == "tool" for body in forwarded for message in body.get("messages", [])))

    def test_streaming_has_anthropic_events(self):
        body = {"model": model_alias("two"), "max_tokens": 128, "stream": True, "messages": [{"role": "user", "content": "stream-probe"}]}
        request = urllib.request.Request(self.store.url() + "/v1/messages", data=json.dumps(body).encode(), headers={**self.headers, "Content-Type": "application/json"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as response:
            text = response.read().decode()
        self.assertIn("message_start", text)
        self.assertIn("content_block_delta", text)
        self.assertIn("message_stop", text)
        self.assertIn("FLEX_OK:upstream-b", text)

    def test_default_change_preserves_stable_model_routes(self):
        self.store.mutate(lambda config: config.update(active="two"))
        render(self.store)
        time.sleep(0.7)  # Backend's configuration watcher debounces file replacement.
        one = self.call("one", "one-after-switch")
        two = self.call("two", "two-after-switch")
        self.assertIn("FLEX_OK:upstream-a", str(one["content"]))
        self.assertIn("FLEX_OK:upstream-b", str(two["content"]))

    def test_start_is_idempotent_and_stop_checks_process_ownership(self):
        pid = self.gateway.owned_pid()
        self.assertEqual(self.gateway.start(), pid)
        self.gateway.stop()
        self.assertIsNone(self.gateway.owned_pid())

    def test_private_account_gateways_continue_running_when_another_account_is_selected(self):
        from claude_flex import accounts, profiles

        self.gateway.stop()
        for label in ("one", "two"):
            accounts.import_account(self.store, label, accounts.normalize({"access": token(label)}))
        one = profiles.prepare(self.store, "one")
        first = Gateway(one)
        first_pid = first.start()
        self.addCleanup(first.stop)
        accounts.select_accounts(self.store, "two")
        two = profiles.prepare(self.store, "two")
        second = Gateway(two)
        second_pid = second.start()
        self.addCleanup(second.stop)
        self.assertEqual(first.owned_pid(), first_pid)
        self.assertNotEqual(one.url(), two.url())
        for store in (one, two):
            response = request_json(store.url() + "/v1/messages", headers={"Authorization": "Bearer " + store.secrets()["gateway_key"], "anthropic-version": "2023-06-01"}, body={"model": model_alias("one"), "max_tokens": 128, "messages": [{"role": "user", "content": "private-route-check"}]})
            self.assertIn("FLEX_OK:upstream-a", str(response["content"]))
        accounts.import_account(self.store, "one", accounts.normalize({"access": token("one", expiry=9999999999)}), replace=True)
        self.assertIsNotNone(first.owned_pid())
        self.assertEqual(second.owned_pid(), second_pid)
        accounts.remove_account(self.store, "one")
        self.assertIsNone(first.owned_pid())
        self.assertEqual(second.owned_pid(), second_pid)
        self.assertNotIn("one", self.store.load()["accounts"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            main(["--home", str(self.store.home), "gateway", "status"])
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "Running")


if __name__ == "__main__":
    unittest.main()
