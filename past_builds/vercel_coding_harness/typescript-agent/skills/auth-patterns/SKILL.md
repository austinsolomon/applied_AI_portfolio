---
description: Patterns and pitfalls for adding authentication to this project
---
# Auth Patterns

Example skill from course lesson 11.1. This project has no authentication yet.

## Where auth would go
- `server.ts`: the web server. Protect `POST /api` and `POST /api/approve`.
- The CLI (`index.ts`) runs locally under the user's own account and needs no auth.

## Recommended approach
- Start with a shared-secret bearer token: read `AUTH_TOKEN` from `.env`, require
  `Authorization: Bearer <token>` on every `/api` route, return 401 otherwise.
- Move to session cookies or OAuth only when more than one person uses the UI.

## Pitfalls
- Never log the token or the `Authorization` header.
- Check auth before starting an agent run, not after: a run costs money.
