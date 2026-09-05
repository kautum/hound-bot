import hashlib
import hmac
import time

import pytest
from cryptography.fernet import Fernet

from app.core.security import REPLAY_WINDOW_SECONDS, TokenCipher, verify_slack_signature

SIGNING_SECRET = "test-signing-secret"


def _slack_signature(body: bytes, timestamp: str, secret: str) -> str:
    """Independently computed oracle, mirroring Slack's documented scheme
    directly — not a call into the function under test."""
    basestring = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


class TestVerifySlackSignature:
    def test_accepts_a_valid_signature(self):
        body = b'{"type":"event_callback"}'
        timestamp = str(int(time.time()))
        signature = _slack_signature(body, timestamp, SIGNING_SECRET)

        assert verify_slack_signature(body, timestamp, signature, SIGNING_SECRET) is True

    def test_rejects_a_tampered_body(self):
        original_body = b'{"type":"event_callback"}'
        timestamp = str(int(time.time()))
        signature = _slack_signature(original_body, timestamp, SIGNING_SECRET)

        tampered_body = b'{"type":"event_callback","extra":"injected"}'
        assert (
            verify_slack_signature(tampered_body, timestamp, signature, SIGNING_SECRET)
            is False
        )

    def test_rejects_wrong_secret(self):
        body = b'{"type":"event_callback"}'
        timestamp = str(int(time.time()))
        signature = _slack_signature(body, timestamp, "a-different-secret")

        assert verify_slack_signature(body, timestamp, signature, SIGNING_SECRET) is False

    def test_rejects_replayed_old_timestamp(self):
        body = b'{"type":"event_callback"}'
        old_timestamp = str(int(time.time()) - REPLAY_WINDOW_SECONDS - 60)
        signature = _slack_signature(body, old_timestamp, SIGNING_SECRET)

        assert verify_slack_signature(body, old_timestamp, signature, SIGNING_SECRET) is False

    def test_accepts_timestamp_at_the_edge_of_the_window(self):
        body = b'{"type":"event_callback"}'
        timestamp = str(int(time.time()) - REPLAY_WINDOW_SECONDS + 10)
        signature = _slack_signature(body, timestamp, SIGNING_SECRET)

        assert verify_slack_signature(body, timestamp, signature, SIGNING_SECRET) is True

    def test_rejects_non_numeric_timestamp(self):
        assert (
            verify_slack_signature(b"{}", "not-a-number", "v0=whatever", SIGNING_SECRET)
            is False
        )

    def test_raises_if_signing_secret_missing(self):
        with pytest.raises(RuntimeError):
            verify_slack_signature(b"{}", str(int(time.time())), "v0=whatever", "")


class TestTokenCipher:
    def test_round_trips_plaintext(self):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)

        ciphertext, version = cipher.encrypt("xoxb-super-secret-bot-token")

        assert version == 1
        assert cipher.decrypt(ciphertext, version) == "xoxb-super-secret-bot-token"

    def test_ciphertext_is_valid_fernet_output_independently(self):
        """Decrypt with a raw Fernet instance, not our class — proves the
        ciphertext is genuine Fernet output, not something our wrapper fakes."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)

        ciphertext, _ = cipher.encrypt("a-refresh-token")

        oracle = Fernet(key.encode())
        assert oracle.decrypt(ciphertext).decode() == "a-refresh-token"

    def test_supports_decrypting_an_older_key_version_during_rotation(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        old_cipher = TokenCipher(keys={1: old_key}, current_version=1)
        old_ciphertext, old_version = old_cipher.encrypt("secret-from-before-rotation")

        rotated_cipher = TokenCipher(keys={1: old_key, 2: new_key}, current_version=2)

        new_ciphertext, new_version = rotated_cipher.encrypt("secret-after-rotation")
        assert new_version == 2

        assert (
            rotated_cipher.decrypt(old_ciphertext, old_version)
            == "secret-from-before-rotation"
        )
        assert rotated_cipher.decrypt(new_ciphertext, new_version) == "secret-after-rotation"

    def test_decrypting_with_unknown_key_version_raises(self):
        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        ciphertext, _ = cipher.encrypt("value")

        with pytest.raises(ValueError):
            cipher.decrypt(ciphertext, key_version=99)

    def test_decrypting_with_wrong_key_for_its_own_version_raises(self):
        real_key = Fernet.generate_key().decode()
        wrong_key = Fernet.generate_key().decode()

        cipher = TokenCipher(keys={1: real_key}, current_version=1)
        ciphertext, version = cipher.encrypt("value")

        mismatched_cipher = TokenCipher(keys={1: wrong_key}, current_version=1)
        with pytest.raises(ValueError):
            mismatched_cipher.decrypt(ciphertext, version)

    def test_constructor_rejects_missing_current_version(self):
        with pytest.raises(ValueError):
            TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=2)
