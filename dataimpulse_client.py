"""Thin async client for the DataImpulse User API.

Base URL: https://gw.dataimpulse.com:777/api  (Basic Auth with the plan login/password)
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

BASE_URL = os.environ.get("DATAIMPULSE_BASE_URL", "https://gw.dataimpulse.com:777/api")


class DataImpulseError(RuntimeError):
    """Raised for any failure talking to DataImpulse, with a message safe to show the user."""


def clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop unset values and convert bools/lists into the forms the API expects."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = int(value)
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        out[key] = value
    return out


_COUNTRY_RE = re.compile(r"cr\.([a-z,]+)", re.IGNORECASE)


def parse_proxy_lines(text: str) -> list[dict[str, Any]]:
    """Parse a proxy list in the default format ``login:password@hostname:port``.

    Lines in any other format are returned with only their ``raw`` value.
    """
    proxies: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        entry: dict[str, Any] = {"raw": line}
        if "@" in line:
            creds, _, hostport = line.rpartition("@")
            login, _, password = creds.partition(":")
            host, _, port = hostport.rpartition(":")
            entry.update(
                login=login,
                password=password,
                host=host,
                port=int(port) if port.isdigit() else port,
            )
            match = _COUNTRY_RE.search(login)
            if match:
                entry["countries"] = match.group(1).lower().split(",")
        proxies.append(entry)
    return proxies


class DataImpulseClient:
    def __init__(self, login: str, password: str, base_url: str = BASE_URL, timeout: float = 30.0):
        if not login or not password:
            raise DataImpulseError("DATAIMPULSE_LOGIN and DATAIMPULSE_PASSWORD must both be set.")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            auth=(login, password),
            timeout=timeout,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None, *, expect_json: bool = True) -> Any:
        try:
            response = await self._client.get(path.lstrip("/"), params=clean_params(params or {}))
        except httpx.HTTPError as exc:
            raise DataImpulseError(f"Could not reach DataImpulse: {exc}") from exc

        if response.status_code == 401:
            raise DataImpulseError(
                "DataImpulse rejected the credentials (HTTP 401). "
                "Check DATAIMPULSE_LOGIN and DATAIMPULSE_PASSWORD on the server."
            )
        if response.status_code >= 400:
            raise DataImpulseError(f"DataImpulse returned HTTP {response.status_code}: {response.text[:500]}")

        if not expect_json:
            return response.text
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    async def aclose(self) -> None:
        await self._client.aclose()
