# Binder Studio FastAPI wrapper

This service turns the reusable backend portion of
[`jaredcasarez/binderpkg`](https://github.com/jaredcasarez/binderpkg) into an
HTTP API. The Qt desktop UI is intentionally not imported.

## Local development

```bash
git clone https://github.com/jaredcasarez/binderpkg.git ../binderpkg
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a; . ./.env; set +a
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the generated OpenAPI client.

## API

- `GET /health`
- `GET|POST /api/v1/binders`
- `GET|PUT|DELETE /api/v1/binders/{id}`
- `GET /api/v1/cards/search?q=bulbasaur`
- `GET /api/v1/cards/resolve?url=https://limitlesstcg.com/cards/...`

Binder documents use the desktop app's JSON shape, so existing project files
can be sent as `document` without translating page or slot data.

## Deploy

Build from the backend directory:

```bash
docker build -t binder-studio-api .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql+psycopg://user:pass@host/db \
  -e CORS_ORIGINS=https://your-frontend.example \
  binder-studio-api
```

Use a managed Postgres URL for durable production data. Set `CORS_ORIGINS` to
a comma-separated list of exact frontend origins. The image can be deployed
unchanged to Render, Railway, Fly.io, Cloud Run, or another container host.
