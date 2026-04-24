# Timepoint AI — Documentation

Source for [docs.timepointai.com](https://docs.timepointai.com). Built with [Mintlify](https://mintlify.com/).

## Start Here

If you're reading these files directly (not the rendered site), begin with these four pages — they cover the shortest path from zero to a working integration:

| Page | What's in it |
|------|--------------|
| [`introduction.mdx`](./introduction.mdx) | What Timepoint is, the service map, and the architecture diagram |
| [`quickstart.mdx`](./quickstart.mdx) | Render your first moment via the hosted API; local-dev setup for Flash, Pro, and Clockchain |
| [`api-reference/authentication.mdx`](./api-reference/authentication.mdx) | The four credential types: Bearer JWT, API Key, X-Service-Key, X-Admin-Key (anchor: `#auth-schemes`) |
| [`api-reference/mcp.mdx`](./api-reference/mcp.mdx) | The Clockchain MCP server + the six tools it exposes (anchor: `#mcp-tools`) |
| [`errors.mdx`](./errors.mdx) | HTTP status codes, rate-limit headers, decision tree for common failures |

## Repo Layout

```
docs/
├── introduction.mdx          Landing page — service map + architecture
├── quickstart.mdx            Hosted-API curl + local dev
├── concepts.mdx              Timepoints, SNAG, temporal modes
├── errors.mdx                Error reference + decision tree
├── accessibility.mdx         WCAG conformance
├── api-reference/
│   ├── overview.mdx          Gateway front-door, domain map, rate limits
│   ├── authentication.mdx    Auth schemes, OAuth providers, health
│   ├── gateway.mdx           Gateway endpoints
│   ├── flash.mdx             Flash rendering API
│   ├── clockchain.mdx        Clockchain read/write API
│   ├── pro.mdx               Pro / SNAG simulation API
│   └── mcp.mdx               MCP server + tools
├── products/                 Product deep dives (Flash, Pro, Clockchain, …)
├── logo/                     Brand assets
└── docs.json                 Mintlify navigation + theme config
```

## Navigation

The sidebar is defined in [`docs.json`](./docs.json) under `navigation.groups`. Add a new page by:

1. Dropping the `.mdx` file in the right folder.
2. Adding its slug (no `.mdx` extension) to the appropriate `pages` array in `docs.json`.

## Local Preview

```bash
npm i -g mintlify
mintlify dev
```

Opens the site at http://localhost:3000.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Licensed under Apache-2.0 — see [`LICENSE`](./LICENSE).
