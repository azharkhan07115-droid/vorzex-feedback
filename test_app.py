import hashlib
import hmac
import io
import os
import struct
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:A12345678901234567890123456789012345")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("VORZEX_FEEDBACK_APP_SECRET", "test-secret-32-bytes-minimum-value")

import app
import keep_alive


class FakeTelegramResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"ok": True}


class FeedbackProxyTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with app._lock:
            app._seen_nonces.clear()
            app._rate.clear()

    @staticmethod
    def png() -> bytes:
        return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 720, 1600)

    def signed(self, nonce: str, signature_override: str | None = None):
        image = self.png()
        caption = "VORZEX ENGINE • LAUNCH FEEDBACK\nDevice: Test Phone\nDate: now"
        timestamp = str(int(time.time()))
        digest = hashlib.sha256(image).hexdigest()
        payload = f"18\n{timestamp}\n{nonce}\nlaunch\n{digest}\n{caption}".encode()
        signature = hmac.new(app.APP_SECRET, payload, hashlib.sha256).hexdigest()
        return self.client.post(
            "/feedback",
            headers={
                "X-Vorzex-Timestamp": timestamp,
                "X-Vorzex-Nonce": nonce,
                "X-Vorzex-Signature": signature_override or signature,
                "X-Vorzex-Version": "18",
            },
            data={"kind": "launch", "caption": caption, "photo": (io.BytesIO(image), "launch.png")},
            content_type="multipart/form-data",
        )

    @patch("app._telegram_send", return_value=FakeTelegramResponse())
    def test_signed_real_data_is_forwarded(self, send):
        response = self.signed("A_secure_unique_nonce_0001")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual("launch", send.call_args.args[2])

    def test_bad_signature_is_rejected(self):
        response = self.signed("A_secure_unique_nonce_0002", "0" * 64)
        self.assertEqual(401, response.status_code)

    def test_custom_picture_with_copied_signature_is_rejected(self):
        image = self.png()
        caption = "VORZEX ENGINE • LAUNCH FEEDBACK\nDevice: Test Phone\nDate: now"
        timestamp = str(int(time.time()))
        nonce = "A_secure_unique_nonce_0004"
        digest = hashlib.sha256(image).hexdigest()
        payload = f"18\n{timestamp}\n{nonce}\nlaunch\n{digest}\n{caption}".encode()
        signature = hmac.new(app.APP_SECRET, payload, hashlib.sha256).hexdigest()
        tampered = image[:-1] + bytes([image[-1] ^ 1])
        response = self.client.post(
            "/feedback",
            headers={
                "X-Vorzex-Timestamp": timestamp,
                "X-Vorzex-Nonce": nonce,
                "X-Vorzex-Signature": signature,
                "X-Vorzex-Version": "18",
            },
            data={"kind": "launch", "caption": caption,
                  "photo": (io.BytesIO(tampered), "custom.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(401, response.status_code)

    @patch("app._telegram_send", return_value=FakeTelegramResponse())
    def test_nonce_replay_is_rejected(self, _send):
        nonce = "A_secure_unique_nonce_0003"
        self.assertEqual(200, self.signed(nonce).status_code)
        self.assertEqual(429, self.signed(nonce).status_code)


class KeepAliveTest(unittest.TestCase):
    def test_adds_health_path(self):
        self.assertEqual(
            "https://vorzex-feedback.onrender.com/health",
            keep_alive.health_url("https://vorzex-feedback.onrender.com"),
        )

    def test_converts_feedback_path(self):
        self.assertEqual(
            "https://vorzex-feedback.onrender.com/health",
            keep_alive.health_url("https://vorzex-feedback.onrender.com/feedback"),
        )

    def test_rejects_non_https_url(self):
        with self.assertRaises(ValueError):
            keep_alive.health_url("http://vorzex-feedback.onrender.com")


if __name__ == "__main__":
    unittest.main()
