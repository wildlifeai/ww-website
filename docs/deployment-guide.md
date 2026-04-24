# Deployment Guide

How to deploy the Wildlife Watcher V2 backend to production.

## Table of Contents

- [Deployment Options](#deployment-options)
- [Option 1: Render (Recommended)](#option-1-render-recommended)
- [Option 2: Docker on Any VPS](#option-2-docker-on-any-vps)
- [Option 3: Manual Process Manager](#option-3-manual-process-manager)
- [Environment Variables](#environment-variables)
- [Supabase Setup](#supabase-setup)
- [DNS and Reverse Proxy](#dns-and-reverse-proxy)
- [Monitoring](#monitoring)
- [Scaling](#scaling)
- [Troubleshooting](#troubleshooting)

---

## Deployment Options

| Option | Best For | Cost | Complexity |
|--------|----------|------|------------|
| **Render Blueprint** | Production — zero-config deploy | ~$21/mo (3 Starter services) | ⭐ Low |
| **Docker on VPS** | Self-hosted, full control | VPS cost + Redis | ⭐⭐ Medium |
| **Manual** | Development, testing | Free | ⭐⭐⭐ High |

---

## Option 1: Render (Recommended)

The repository includes a `render.yaml` Blueprint that provisions all three services in one click.

### Steps

1. **Push the branch to GitHub**

   ```bash
   git push origin feat/v2-migration
   ```

2. **Connect on Render**

   - Go to [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**
   - Select the `wildlifeai/ww-website` repository
   - Select the `feat/v2-migration` branch
   - Render auto-detects `render.yaml` and shows 3 services:
     - `wildlife-watcher-api` (Web Service)
     - `wildlife-watcher-worker` (Background Worker)
     - `wildlife-watcher-redis` (Redis)

3. **Configure environment variables**

   Render will prompt for variables marked `sync: false`:

   | Variable | Where to Find |
   |----------|--------------|
   | `SUPABASE_URL` | Supabase Dashboard → Settings → API → Project URL |
   | `SUPABASE_ANON_KEY` | Supabase Dashboard → Settings → API → `anon` `public` |
   | `SUPABASE_SERVICE_ROLE_KEY` | Supabase Dashboard → Settings → API → `service_role` |
   | `GENERAL_ORG_ID` | Your organisation UUID from the `organisations` table |
   | `SENTRY_DSN` | Sentry project DSN (optional) |

   `REDIS_URL` and `LORAWAN_WEBHOOK_SECRET` are auto-provisioned.

4. **Deploy**

   Click **Apply** — Render builds all services in parallel.

5. **Verify**

   ```bash
   curl https://wildlife-watcher-api.onrender.com/health
   # → {"status": "ok"}
   ```

### Custom Domain

1. Go to the `wildlife-watcher-api` service → **Settings** → **Custom Domains**
2. Add `api.wildlifewatcher.ai`
3. Set up DNS: `CNAME api.wildlifewatcher.ai → wildlife-watcher-api.onrender.com`
4. Update `ALLOWED_ORIGINS` to include your frontend domain

---

## Option 2: Docker on Any VPS

### Prerequisites

- Ubuntu 22.04+ (or any Docker-compatible OS)
- Docker Engine 24+ and Docker Compose v2
- At least 1 GB RAM, 10 GB disk

### Steps

1. **Clone and configure**

   ```bash
   git clone https://github.com/wildlifeai/ww-website.git
   cd ww-website
   cp .env.example .env
   # Edit .env with your Supabase keys
   ```

2. **Build and start**

   ```bash
   docker compose up -d --build
   ```

3. **Verify**

   ```bash
   # Health check
   curl http://localhost:8000/health

   # View logs
   docker compose logs -f api worker

   # Check Redis
   docker compose exec redis redis-cli ping
   ```

4. **Set up a reverse proxy** (see [DNS and Reverse Proxy](#dns-and-reverse-proxy))

### Updating

```bash
git pull origin feat/v2-migration
docker compose up -d --build
```

### Resource Requirements

| Service | CPU | RAM | Disk |
|---------|-----|-----|------|
| API | 0.5 vCPU | 256 MB | — |
| Worker | 1 vCPU | 512 MB | 1 GB (temp files) |
| Redis | 0.25 vCPU | 128 MB | 100 MB |

---

## Option 3: Manual Process Manager

For development servers or testing.

```bash
# Install dependencies
cd backend
pip install -r requirements.txt

# Start Redis
docker run -d -p 6379:6379 --name ww-redis redis:7-alpine

# Start API (production mode)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

# Start Worker (in a separate terminal or via systemd)
arq app.jobs.worker.WorkerSettings
```

### systemd Service Files

**`/etc/systemd/system/ww-api.service`:**

```ini
[Unit]
Description=Wildlife Watcher API
After=network.target redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ww-website/backend
EnvironmentFile=/opt/ww-website/.env
ExecStart=/opt/ww-website/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/ww-worker.service`:**

```ini
[Unit]
Description=Wildlife Watcher ARQ Worker
After=network.target redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ww-website/backend
EnvironmentFile=/opt/ww-website/.env
ExecStart=/opt/ww-website/backend/venv/bin/arq app.jobs.worker.WorkerSettings
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ww-api ww-worker
sudo systemctl start ww-api ww-worker
```

---

## Environment Variables

See the [backend README](../backend/README.md#configuration) for the complete variable reference.

**Critical production checklist:**

- [ ] `SUPABASE_SERVICE_ROLE_KEY` is set and kept secret
- [ ] `ALLOWED_ORIGINS` is set to your exact frontend domain(s)
- [ ] `LORAWAN_WEBHOOK_SECRET` is set (not empty)
- [ ] `SENTRY_DSN` is set for error tracking
- [ ] `LOG_LEVEL` is `info` (not `debug`)
- [ ] `RATE_LIMIT_PER_MINUTE` is appropriate for your traffic

---

## Supabase Setup

The backend expects these Supabase resources:

### Storage Buckets

| Bucket | Purpose | Public |
|--------|---------|--------|
| `firmware` | Config firmware, manifest results | No |
| `ai-models` | AI model ZIPs | No |

Create them in Supabase Dashboard → Storage → New Bucket.

### Database Tables

The backend reads/writes these tables (created by the mobile app's migration schema):

| Table | Used By | Access |
|-------|---------|--------|
| `devices` | LoRaWAN domain (device lookup by EUI) | RLS + service-role |
| `deployments` | LoRaWAN domain (active deployment match) | RLS + service-role |
| `ai_models` | Model domain (register/update) | RLS + service-role |
| `firmware` | Manifest domain (config firmware lookup) | RLS + service-role |
| `user_roles` | Dependencies (permission checks) | RLS + service-role |
| `lorawan_messages` | LoRaWAN domain (raw message store) | service-role only |
| `lorawan_parsed_messages` | LoRaWAN domain (parsed data store) | service-role only |

### RPC Functions

| Function | Purpose |
|----------|---------|
| `check_user_uploader_role(p_user_id, p_org_id)` | Verifies user has upload permission |

### Realtime

Enable Realtime on `lorawan_parsed_messages` so the mobile app receives live updates:

1. Supabase Dashboard → Database → Replication
2. Enable `lorawan_parsed_messages` table for Realtime

---

## DNS and Reverse Proxy

### Nginx Configuration

```nginx
server {
    listen 443 ssl http2;
    server_name api.wildlifewatcher.ai;

    ssl_certificate /etc/letsencrypt/live/api.wildlifewatcher.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.wildlifewatcher.ai/privkey.pem;

    # Proxy to FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Pass through request ID
        proxy_set_header X-Request-ID $request_id;

        # WebSocket support (for future use)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Increase timeouts for file uploads
        proxy_read_timeout 120;
        client_max_body_size 50M;
    }
}
```

### Cloudflare

If using Cloudflare as reverse proxy:

1. Add A record for `api.wildlifewatcher.ai` → your VPS IP
2. Enable **Proxied** (orange cloud)
3. SSL/TLS → Full (strict)
4. Under **Rules** → increase the upload limit to 100 MB for `/api/models/convert`

---

## Monitoring

### Health Checks

The API exposes `/health` for automated monitoring:

```bash
# Simple check
curl -f http://localhost:8000/health || echo "API is down"

# Uptime monitoring services (UptimeRobot, Better Uptime, etc.)
# URL: https://api.wildlifewatcher.ai/health
# Interval: 60 seconds
# Expected: 200 OK
```

### Structured Logging

All logs are JSON-formatted for easy ingestion into log aggregators:

```json
{
  "event": "request_complete",
  "method": "POST",
  "path": "/api/manifest/generate",
  "status_code": 200,
  "duration_ms": 42.3,
  "request_id": "a1b2c3d4-..."
}
```

Supported log aggregators:
- **AWS CloudWatch** (via Docker log driver)
- **Datadog** (via structured JSON)
- **Grafana Loki** (via Promtail)
- **Render Logs** (built-in on Render)

### Sentry

For error tracking, set `SENTRY_DSN` to your Sentry project DSN:

```
SENTRY_DSN=https://abc123@o456.ingest.sentry.io/789
```

This automatically captures:
- Unhandled exceptions
- Performance traces (10% sample rate)
- Request context (URL, method, headers)

### Redis Monitoring

```bash
# Check Redis connectivity
docker compose exec redis redis-cli ping

# View memory usage
docker compose exec redis redis-cli info memory

# List active jobs
docker compose exec redis redis-cli keys "arq:*"

# Check job count
docker compose exec redis redis-cli keys "job:*" | wc -l
```

---

## Scaling

### Horizontal Scaling

| Component | Strategy |
|-----------|----------|
| **API** | Add more workers (`--workers 4`) or more containers |
| **Worker** | Run multiple worker containers (ARQ auto-distributes) |
| **Redis** | For >1000 jobs/min, consider Redis Cluster or higher-memory plan |

### Render Scaling

```yaml
# In render.yaml, change plan to scale
services:
  - type: web
    name: wildlife-watcher-api
    plan: standard  # or professional
```

### Docker Compose Scaling

```bash
# Scale workers
docker compose up -d --scale worker=3
```

---

## Troubleshooting

### Common Issues

**App won't start: `ValidationError` on startup**

Missing required environment variables. Check that `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are set.

```bash
# Verify env vars are loaded
docker compose exec api env | grep SUPABASE
```

**Jobs stuck in `queued` status**

The worker is not running or can't connect to Redis.

```bash
# Check worker logs
docker compose logs worker

# Verify Redis connectivity
docker compose exec worker python -c "import redis; r=redis.from_url('redis://redis:6379'); print(r.ping())"
```

**Model conversion fails: "Vela command not found"**

The `ethos-u-vela` package needs to be installed in the worker container. Verify it's in `requirements.txt` and the Docker image was rebuilt.

```bash
docker compose exec worker vela --version
```

**Rate limiting is too aggressive**

Increase the limit:

```
RATE_LIMIT_PER_MINUTE=120
```

**CORS errors from frontend**

Add your frontend origin to `ALLOWED_ORIGINS`:

```
ALLOWED_ORIGINS=https://your-app.com,http://localhost:5173
```

**LoRaWAN webhooks returning 401**

Webhook secret mismatch. Verify the secret matches between your network server and `LORAWAN_TTN_WEBHOOK_SECRET` / `LORAWAN_CHIRPSTACK_WEBHOOK_SECRET`.

```bash
# Check configured secret
docker compose exec api python -c "from app.config import settings; print(settings.LORAWAN_TTN_WEBHOOK_SECRET[:4] + '...')"
```
