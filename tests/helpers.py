import base64
import json
import socket
import time


def token(account="account-one", *, expiry=None):
    payload = {
        "exp": expiry if expiry is not None else time.time() + 86400 * 5,
        "https://api.openai.com/auth": {"chatgpt_account_id": account, "chatgpt_plan_type": "plus"},
        "https://api.openai.com/profile": {"email": "tester@example.invalid"},
    }
    part = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "test." + part + ".not-a-real-signature"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
