"""MSAL public-client auth code + PKCE, invoked only by the private parent pipe."""

import hashlib
import json
import logging
import sys
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from keyring.backend import KeyringBackend

SERVICE = "Jarvis/Outlook"
# One account, one consent: the calendar rides on the same token as the mailbox.
SCOPES = ["User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"]
CHUNKS = 64


class OAuthHTTP:
    """MSAL's documented HTTP seam, restricted to Microsoft's fixed authority."""

    def __init__(self) -> None:
        import requests

        self.session = requests.Session()
        self.session.trust_env = False

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "login.microsoftonline.com"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
        ):
            raise ValueError("authority")
        self.session.cookies.clear()
        kwargs.update(timeout=10, allow_redirects=False, stream=True)
        response = self.session.request(method, url, **kwargs)
        try:
            data = bytearray()
            for chunk in response.iter_content(16384):
                data.extend(chunk)
                if len(data) > 262144:
                    raise ValueError("oauth response limit")
            if 300 <= response.status_code < 400:
                raise ValueError("oauth redirect")
            # requests' cached response is what MSAL's documented response interface reads.
            response._content = bytes(data)
            return response
        finally:
            response.close()
            self.session.cookies.clear()


class CacheStore:
    """ASCII chunks fit Windows Credential Locker's per-credential size limit.

    Invalidate the manifest before writes. Partial writes are never readable. A bounded
    fixed slot set allows disconnect to remove interrupted writes as well as live cache.
    """

    def __init__(self, backend: KeyringBackend) -> None:
        self.backend = backend

    def load(self) -> str:
        manifest = self.backend.get_password(SERVICE, "manifest")
        if not manifest:
            return ""
        count, digest = json.loads(manifest)
        if type(count) is not int or not 1 <= count <= CHUNKS:
            raise ValueError("cache")
        raw = "".join(self.backend.get_password(SERVICE, str(i)) or "" for i in range(count))
        if len(raw) > CHUNKS * 1000 or hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise ValueError("cache")
        return raw

    def save(self, raw: str) -> None:
        if not raw.isascii() or not 1 <= len(raw) <= CHUNKS * 1000:
            raise ValueError("cache")
        self.backend.set_password(SERVICE, "manifest", "")
        chunks = [raw[i : i + 1000] for i in range(0, len(raw), 1000)]
        for i in range(CHUNKS):
            # Overwrite old tails too: no refresh token remains in unused live slots.
            self.backend.set_password(SERVICE, str(i), chunks[i] if i < len(chunks) else "")
        self.backend.set_password(
            SERVICE, "manifest", json.dumps([len(chunks), hashlib.sha256(raw.encode()).hexdigest()])
        )

    def clear(self) -> None:
        from keyring.errors import PasswordDeleteError

        self.backend.set_password(SERVICE, "manifest", "")
        failed = False
        for key in [*(str(i) for i in range(CHUNKS)), "manifest"]:
            try:
                if self.backend.get_password(SERVICE, key) is not None:
                    self.backend.delete_password(SERVICE, key)
            except PasswordDeleteError:
                failed = True
        if failed:
            raise ValueError("cache")


def authenticate(operation: str, client_id: str, home_id: str, store: CacheStore) -> dict[str, str]:
    if operation == "disconnect":
        store.clear()
        return {}
    UUID(client_id)
    if operation not in ("connect", "silent") or len(home_id) > 512:
        raise ValueError("operation")
    import msal  # type: ignore[import-untyped]

    cache = msal.SerializableTokenCache()
    if operation == "silent":
        record = json.loads(store.load())
        if record["client_id"] != client_id:
            raise ValueError("account")
        cache.deserialize(record["cache"])
    else:
        # Verify writable OS storage before opening user sign-in.
        store.save(json.dumps({"client_id": client_id, "cache": "{}"}))
    http = OAuthHTTP()
    app = msal.PublicClientApplication(
        client_id,
        authority="https://login.microsoftonline.com/common",
        token_cache=cache,
        http_client=http,
        timeout=10,
        enable_pii_log=False,
        enable_broker_on_windows=False,
        enable_broker_on_mac=False,
    )
    result: Any
    if operation == "connect":
        result = app.acquire_token_interactive(scopes=SCOPES, timeout=120, prompt="select_account")
    else:
        accounts = [a for a in app.get_accounts() if a.get("home_account_id") == home_id]
        if len(accounts) != 1:
            raise ValueError("account")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not isinstance(result, dict) or "access_token" not in result:
        raise ValueError("authorization")
    accounts = app.get_accounts()
    if len(accounts) != 1 or (home_id and accounts[0]["home_account_id"] != home_id):
        raise ValueError("account")
    store.save(json.dumps({"client_id": client_id, "cache": cache.serialize()}, ensure_ascii=True))
    return {"home_id": accounts[0]["home_account_id"], "token": result["access_token"]}


def main() -> int:
    logging.disable(logging.CRITICAL)
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 1
    try:
        from jarvis.platforms.credentials import native_store

        raw = sys.stdin.buffer.readline(2049)
        if len(raw) > 2048:
            return 1
        operation, client_id, home_id = json.loads(raw)
        if not all(isinstance(v, str) for v in (operation, client_id, home_id)):
            return 1
        result = authenticate(operation, client_id, home_id, CacheStore(native_store()))
        encoded = json.dumps(result)
        if len(encoded) > 64000:
            return 1
        sys.stdout.write(encoded + "\n")
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
