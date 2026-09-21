# DataImpulse MCP connector

A small remote MCP server that lets Claude manage a DataImpulse proxy plan: check traffic, inspect pool supply, generate sticky proxy lists and reset sessions. Add it to Claude as a custom connector.

## Tools

| Tool | DataImpulse endpoint | What it does |
|---|---|---|
| `get_plan_stats` | `/api/stats` | Total, used and remaining traffic |
| `get_stats_history` | `/api/stats_with_history` | Traffic over time |
| `get_error_stats` | `/api/errors_stats_with_parameters` | Error counts by time, host, type |
| `get_usage_stats` | `/api/usage` | Usage by domain or by sticky session |
| `get_pool_stats` | `/api/pool_stats_with_parameters` | Live supply by country, city, ASN, zip, state |
| `get_proxy_list` | `/api/list` | Generate proxies. Defaults to **sticky + socks5** (the API defaults to rotating + http) |
| `rotate_ip` | `/api/rotate_ip` | Reset one sticky session to a new IP. Marked destructive, so Claude asks first |

## 1. Deploy

Any host that runs a Docker container on a public HTTPS URL works: Railway, Render, Fly.io, or your own VPS behind a reverse proxy. Claude connects from Anthropic's cloud, so the server must be reachable from the public internet.

Set three environment variables on the host (see `.env.example`):

- `DATAIMPULSE_LOGIN` and `DATAIMPULSE_PASSWORD`: your plan credentials
- `CONNECTOR_TOKEN`: a long random secret. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`

Most hosts inject `PORT` automatically. The container listens on it (default 8000).

## 2. Verify before connecting

Locally, with a `.env` file filled in:

```
docker build -t dataimpulse-mcp .
docker run --rm --env-file .env dataimpulse-mcp python server.py --check
```

`OK` plus your plan stats means the credentials work. Once deployed, `https://<your-host>/healthz` should return `ok`.

## 3. Add to Claude

The connector URL is:

```
https://<your-host>/<CONNECTOR_TOKEN>/mcp
```

- **Pro / Max:** in Claude, click **+** then **Add custom connector**, and paste the URL.
- **Team / Enterprise:** an Owner adds it for the organisation (**Add**, then **Custom**, then **Web**), then each member clicks **Connect**.

Enable it per conversation from the **+** menu under **Connectors**.
Reference: https://support.claude.com/en/articles/11175166

## Security

- **The URL is the key.** Anyone with it can generate proxy lists (which contain your proxy credentials) and reset sessions. Keep it private. If it leaks, change `CONNECTOR_TOKEN` and re-add the connector.
- Proxy-list output contains credentials by design, since it is meant to be imported into tools like GeeLark.
- Credentials live only in the server's environment variables, never in the code.
- For stronger protection, put the server behind OAuth; Claude custom connectors accept an OAuth client ID and secret under Advanced settings.

## Troubleshooting

- **HTTP 421 / invalid Host header:** FastMCP's DNS-rebinding protection is rejecting your public hostname. The server sets `host="0.0.0.0"` to avoid this; check your MCP SDK version if it still happens.
- **`DataImpulse rejected the credentials (HTTP 401)`:** wrong login/password on the server.
- **Timeouts reaching DataImpulse:** the API runs on port 777; make sure your host allows outbound connections to it.
- **Claude can't connect:** the URL must be HTTPS and publicly reachable, and must end in `/mcp`.
