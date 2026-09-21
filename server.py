"""DataImpulse remote MCP server, for use as a Claude custom connector.

The MCP endpoint is served at  https://<your-host>/<CONNECTOR_TOKEN>/mcp
Everything else returns 404, except /healthz for your host's health checks.

Run `python server.py --check` to verify credentials without starting the server.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from typing import Annotated, Any, Literal, Optional

from pydantic import Field
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from dataimpulse_client import DataImpulseClient, DataImpulseError, parse_proxy_lines

# host="0.0.0.0" matters: when FastMCP believes it is bound to localhost it turns on
# DNS-rebinding protection that rejects requests carrying a public Host header.
mcp = FastMCP(
    "DataImpulse",
    host="0.0.0.0",
    stateless_http=True,
    json_response=True,
    instructions=(
        "Manage a DataImpulse residential proxy plan: check remaining traffic, inspect pool "
        "supply by country/city/ASN, generate proxy lists and reset sticky sessions. "
        "For accounts that must keep one identity, generate type=sticky proxies, one country per "
        "request, with the longest session_ttl the plan allows, and check supply with "
        "get_pool_stats before generating."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
CHANGES_IP = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

_client: DataImpulseClient | None = None


def client() -> DataImpulseClient:
    global _client
    if _client is None:
        _client = DataImpulseClient(
            os.environ.get("DATAIMPULSE_LOGIN", ""),
            os.environ.get("DATAIMPULSE_PASSWORD", ""),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call(path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    try:
        return await client().get(path, params, **kwargs)
    except DataImpulseError as exc:
        return {"error": str(exc)}


# Shared parameter types -----------------------------------------------------------------

CSV = Optional[str]
DateFrom = Annotated[Optional[str], Field(description="Start datetime, e.g. '2026-09-01 00:00:00'. API default applies if omitted.")]
DateTo = Annotated[Optional[str], Field(description="End datetime. Defaults to now.")]


# Tools --------------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
async def get_plan_stats() -> Any:
    """Plan traffic totals: total, used and remaining traffic, thread count and status."""
    return await call("stats")


@mcp.tool(annotations=READ_ONLY)
async def get_stats_history(
    date_from: DateFrom = None,
    date_to: DateTo = None,
    group_type: Annotated[
        Optional[Literal["minute", "hour", "day", "month"]],
        Field(description="Grouping for the traffic history. API default: day."),
    ] = None,
) -> Any:
    """Traffic history over time (default: last 30 days, grouped by day)."""
    return await call("stats_with_history", {"from": date_from, "to": date_to, "group_type": group_type})


@mcp.tool(annotations=READ_ONLY)
async def get_error_stats(
    date_from: DateFrom = None,
    date_to: DateTo = None,
    groupby: Annotated[CSV, Field(description="Comma-separated group key from: datetime, host, type. Default: datetime,host.")] = None,
    datetime_interval: Optional[Literal["minute", "hour", "day", "month"]] = None,
    limit: Annotated[Optional[int], Field(ge=1, description="Max rows. Default 1000.")] = None,
    offset: Annotated[Optional[int], Field(ge=0)] = None,
) -> Any:
    """Proxy error counts, groupable by time, target host and error type."""
    return await call(
        "errors_stats_with_parameters",
        {
            "from": date_from,
            "to": date_to,
            "groupby": groupby,
            "datetime_interval": datetime_interval,
            "limit": limit,
            "offset": offset,
        },
    )


@mcp.tool(annotations=READ_ONLY)
async def get_usage_stats(
    groupby: Annotated[
        Optional[Literal["domain", "session"]],
        Field(description="'domain' groups by target host (default); 'session' groups by sticky session."),
    ] = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    limit: Annotated[Optional[int], Field(ge=1, le=10000, description="Default 1000, max 10000.")] = None,
    offset: Annotated[Optional[int], Field(ge=0)] = None,
) -> Any:
    """Traffic usage grouped by target domain or by sticky session."""
    return await call("usage", {"groupby": groupby, "from": date_from, "to": date_to, "limit": limit, "offset": offset})


@mcp.tool(annotations=READ_ONLY)
async def get_pool_stats(
    countries: Annotated[CSV, Field(description="Comma-separated country codes, e.g. 'de' or 'de,it'.")] = None,
    groupby: Annotated[
        Optional[Literal["country", "city", "asn", "zip", "state"]],
        Field(description="How to group available supply. Default: country."),
    ] = None,
    cities: CSV = None,
    states: CSV = None,
    zipcodes: CSV = None,
    asns: CSV = None,
    anonymous: Optional[bool] = None,
    exclude_mobile: Annotated[Optional[bool], Field(description="True to exclude mobile networks.")] = None,
    exclude_countries: CSV = None,
    exclude_asns: CSV = None,
    exclude_cities: CSV = None,
    exclude_states: CSV = None,
    exclude_zipcodes: CSV = None,
    limit: Annotated[Optional[int], Field(ge=1, description="Default 5000.")] = None,
) -> Any:
    """Real-time pool availability. Use before generating proxies to confirm supply exists
    in a given country, city or ASN."""
    return await call(
        "pool_stats_with_parameters",
        {
            "countries": countries,
            "groupby": groupby,
            "cities": cities,
            "states": states,
            "zipcodes": zipcodes,
            "asns": asns,
            "anonymous": anonymous,
            "exclude_mobile": exclude_mobile,
            "exclude_countries": exclude_countries,
            "exclude_asns": exclude_asns,
            "exclude_cities": exclude_cities,
            "exclude_states": exclude_states,
            "exclude_zipcodes": exclude_zipcodes,
            "limit": limit,
        },
    )


@mcp.tool(annotations=READ_ONLY)
async def get_proxy_list(
    quantity: Annotated[int, Field(ge=1, le=1000, description="Number of proxies to return.")] = 3,
    type: Annotated[
        Literal["sticky", "rotating"],
        Field(description="'sticky' keeps one IP per session for session_ttl minutes. Defaults to sticky here (the API itself defaults to rotating)."),
    ] = "sticky",
    protocol: Annotated[Literal["socks5", "http"], Field(description="Defaults to socks5 here (API default: http).")] = "socks5",
    session_ttl: Annotated[Optional[int], Field(ge=1, description="Sticky session lifetime in minutes. API default: 30.")] = None,
    countries: Annotated[CSV, Field(description="Comma-separated country codes. Use ONE country per request for fixed-country proxies.")] = None,
    cities: CSV = None,
    states: CSV = None,
    zipcodes: CSV = None,
    asns: CSV = None,
    anonymous: Optional[bool] = None,
    exclude_countries: CSV = None,
    exclude_asns: Annotated[CSV, Field(description="Comma-separated ASNs to exclude, e.g. hosting providers.")] = None,
    exclude_cities: CSV = None,
    exclude_states: CSV = None,
    exclude_zipcodes: CSV = None,
    format: Annotated[
        Optional[str],
        Field(description="Custom line format using login, password, hostname, port. Leave unset for login:password@hostname:port, which is parsed into fields."),
    ] = None,
) -> Any:
    """Generate a proxy list. Returns each proxy as raw text plus parsed login/password/host/port.

    Note the output contains proxy credentials by design."""
    params = {
        "quantity": quantity,
        "type": type,
        "protocol": protocol,
        "session_ttl": session_ttl,
        "countries": countries,
        "cities": cities,
        "states": states,
        "zipcodes": zipcodes,
        "asns": asns,
        "anonymous": anonymous,
        "exclude_countries": exclude_countries,
        "exclude_asns": exclude_asns,
        "exclude_cities": exclude_cities,
        "exclude_states": exclude_states,
        "exclude_zipcodes": exclude_zipcodes,
        "format": format,
    }
    try:
        text = await client().get("list", params, expect_json=False)
    except DataImpulseError as exc:
        return {"error": str(exc)}
    proxies = parse_proxy_lines(text)
    return {
        "count": len(proxies),
        "request": {k: v for k, v in params.items() if v is not None},
        "proxies": proxies,
    }


@mcp.tool(annotations=CHANGES_IP)
async def rotate_ip(
    port: Annotated[Optional[int], Field(description="Sticky port whose session should be reset.")] = None,
    sessid: Annotated[Optional[str], Field(description="Session ID to reset.")] = None,
) -> Any:
    """Reset a sticky session so it gets a NEW IP. Anything logged in through that session will
    see its IP change, so only use this deliberately (e.g. to replace a bad or datacenter IP)."""
    if (port is None) == (sessid is None):
        return {"error": "Provide exactly one of port or sessid."}
    return await call("rotate_ip", {"port": port, "sessid": sessid})


# App ------------------------------------------------------------------------------------

def create_app() -> Starlette:
    token = os.environ.get("CONNECTOR_TOKEN", "")
    if len(token) < 24:
        raise SystemExit(
            "CONNECTOR_TOKEN must be set to a long random string (24+ characters). Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    mcp_app = mcp.streamable_http_app()  # serves MCP at /mcp; creates mcp.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await close_client()

    async def health(_request):
        return PlainTextResponse("ok")

    return Starlette(
        routes=[
            Route("/healthz", health),
            Mount(f"/{token}", app=mcp_app),
        ],
        lifespan=lifespan,
    )


async def _check() -> int:
    """Verify DataImpulse credentials and reachability, printing plan stats."""
    try:
        stats = await client().get("stats")
    except DataImpulseError as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        await close_client()
    print("OK - DataImpulse reachable, credentials accepted.")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(asyncio.run(_check()))

    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
