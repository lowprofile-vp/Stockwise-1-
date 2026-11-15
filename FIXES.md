# Fixes in this version

## Deployment
- **API crashed on start on Python 3.11 and older** (`app/data/api.py`): a backslash inside an f-string expression is a SyntaxError before Python 3.12, so the whole API failed to import on Render. Rewritten without the f-string. Python is now pinned to 3.12 (`backend/.python-version`, `render.yaml`).
- **Render ignored the Procfile**: added `render.yaml` with the build and start commands, health check and env vars. Gunicorn now runs 2 workers × 4 threads with a 120 s timeout, because the Supabase-backed dashboard can take longer than the default 30 s and workers were being killed.
- **Supabase logins rejected on older projects**: tokens signed with the legacy HS256 secret were refused outright. The API now verifies them with `SUPABASE_JWT_SECRET` if set, or asks Supabase Auth (`/auth/v1/user`), and falls back the same way if the signing keys can't be loaded.
- **CORS**: an origin with a trailing slash (`https://app.vercel.app/`) never matched, so the browser blocked every call. Origins are normalized, `FRONTEND_URL` is always allowed, and patterns like `https://*.vercel.app` work.
- **Frontend API URL**: `VITE_API_URL` is accepted with or without a trailing slash or `/api`, and a missing value logs a clear error in production builds instead of silently calling localhost.
- **Vercel**: `frontend/vercel.json` declares the Vite preset; a root `vercel.json` builds `frontend/` if the project root was left at the repository root.
- Production without Supabase now refuses the built-in dev JWT secret (as the README already said), and creates the tables on first start when using `DATABASE_URL`.

- **Single Vercel deployment**: `vercel.json`, `api/index.py` and `requirements.txt` at the project root deploy the frontend and the Flask API together on one Vercel project (API at `/api`, same origin, no CORS). Production builds default to `/api`, so `VITE_API_URL` is no longer needed there.

## Bugs
- Reset-password page could stay stuck on "Open the reset link…" because the recovery event fired before the page subscribed. It now also checks the existing session.
- `?page=abc`, `?per_page=x` or a non-numeric `product_id` returned a 500. They now fall back to defaults.
- Bulk update/delete through the Data API crashed (or refused) when a join filter matched nothing; now a no-op.
- Missing error handlers on a few dropdown loads (Analytics, Reports) caused unhandled promise rejections.
- `PyJWT` pinned explicitly; test JWT secret lengthened (removes the insecure-key warnings).

## Tests
`tests/test_deploy.py` covers CORS normalization and preflight, both HS256 paths, and the production secret check. 21 tests pass on Python 3.11 and 3.13.
