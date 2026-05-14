from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class ServiceAccountTokenProvider:
    credentials_path: Path
    scopes: list[str]
    _access_token: str | None = None
    _expires_at: float = 0

    def token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        assertion = self._build_jwt_assertion()
        data = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._access_token

    def _build_jwt_assertion(self) -> str:
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": credentials["client_email"],
            "scope": " ".join(self.scopes),
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = ".".join(
            [
                _base64url_json(header),
                _base64url_json(claims),
            ]
        ).encode("ascii")
        signature = _openssl_rs256_sign(signing_input, credentials["private_key"])
        return signing_input.decode("ascii") + "." + _base64url(signature)


def token_provider_from_env(scopes: list[str]) -> ServiceAccountTokenProvider:
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not path and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64"):
        path = _write_credentials_from_env(
            base64.b64decode(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]).decode("utf-8")
        )
    if not path and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_TEXT"):
        path = _write_credentials_from_env(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_TEXT"])
    if not path:
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON to a credential file path, "
            "or GOOGLE_SERVICE_ACCOUNT_JSON_B64 / GOOGLE_SERVICE_ACCOUNT_JSON_TEXT for hosted deploys."
        )
    return ServiceAccountTokenProvider(Path(path), scopes)


def _write_credentials_from_env(value: str) -> str:
    path = Path(tempfile.gettempdir()) / "google-service-account.json"
    path.write_text(value, encoding="utf-8")
    return str(path)


def _base64url_json(value: dict) -> str:
    return _base64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _openssl_rs256_sign(signing_input: bytes, private_key: str) -> bytes:
    with tempfile.NamedTemporaryFile("w", delete=False) as key_file:
        key_file.write(private_key)
        key_path = key_file.name
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise RuntimeError("OpenSSL is required for service-account JWT signing in this dependency-free demo.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="replace")) from exc
    finally:
        try:
            os.unlink(key_path)
        except OSError:
            pass
