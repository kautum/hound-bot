"""The three primitives everything else trusts.

1. `verify_slack_signature` — HMAC-SHA256 over the *raw* request body, with a replay
   window, using constant-time comparison. Every inbound Slack request goes through
   this before any JSON parsing. See ARCHITECTURE.md's security boundary section.
2. `TokenCipher` — Fernet encryption for per-tenant secrets at rest (bot tokens,
   Google refresh tokens), keyed by `key_version` so a key can be rotated by
   re-encrypting gradually rather than in one risky migration.
"""

import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

# S6: token_cipher_from_settings used to build a single-entry key map from
# the one ENCRYPTION_KEY in config — rotating the key would make every
# existing row unreadable (no key available for the old key_version).
# ENCRYPTION_KEY_OLD/_VERSION, when present, keep the previous key available
# for decryption while ENCRYPTION_KEY becomes the new key everything encrypts
# with going forward — the standard "add new, keep old, re-encrypt
# gradually, drop old" rotation shape TokenCipher was already built for.

SLACK_SIGNATURE_VERSION = "v0"
REPLAY_WINDOW_SECONDS = 5 * 60


def verify_slack_signature(
    raw_body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Verify a Slack request per Slack's signing-secret scheme.

    `raw_body` must be the exact bytes of the request body, read before any JSON
    parsing — re-serialising the payload produces different bytes and silently
    breaks this check. Returns False for any invalid/replayed/mismatched request;
    raises only on caller misconfiguration (missing secret), never on attacker input.
    """
    if not signing_secret:
        raise RuntimeError("SLACK_SIGNING_SECRET is not configured")

    try:
        request_time = int(timestamp)
    except (TypeError, ValueError):
        return False

    current_time = now if now is not None else time.time()
    if abs(current_time - request_time) > REPLAY_WINDOW_SECONDS:
        return False

    basestring = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + raw_body
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    expected_signature = f"{SLACK_SIGNATURE_VERSION}={digest}"

    return hmac.compare_digest(expected_signature, signature)


class TokenCipher:
    """Encrypts/decrypts per-tenant secrets, keyed by `key_version`.

    `keys` maps key_version -> Fernet key (base64 string). Encryption always uses
    `current_version`; decryption looks up whichever version the row was encrypted
    with, so old rows keep working while a rotation re-encrypts them gradually.
    """

    def __init__(self, keys: dict[int, str], current_version: int):
        if current_version not in keys:
            raise ValueError(f"current_version={current_version} has no matching key")
        self._fernets = {version: Fernet(key.encode()) for version, key in keys.items()}
        self._current_version = current_version

    def encrypt(self, plaintext: str) -> tuple[bytes, int]:
        fernet = self._fernets[self._current_version]
        return fernet.encrypt(plaintext.encode()), self._current_version

    def decrypt(self, ciphertext: bytes, key_version: int) -> str:
        try:
            fernet = self._fernets[key_version]
        except KeyError:
            raise ValueError(f"no key available for key_version={key_version}") from None
        try:
            return fernet.decrypt(ciphertext).decode()
        except InvalidToken:
            raise ValueError(
                "ciphertext is invalid or was encrypted with a different key"
            ) from None


def token_cipher_from_settings(settings: Settings) -> TokenCipher:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    keys = {settings.encryption_key_version: settings.encryption_key}
    if settings.encryption_key_old and settings.encryption_key_old_version is not None:
        keys[settings.encryption_key_old_version] = settings.encryption_key_old
    return TokenCipher(
        keys=keys,
        current_version=settings.encryption_key_version,
    )
