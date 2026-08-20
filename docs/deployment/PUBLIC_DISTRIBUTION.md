# Public distribution — Docker on your web server

Deploy PocketPro:NYL for **subscribing customers** on a VPS or dedicated server. Customers access the app over HTTPS; backend and ChromaDB stay on a private Docker network.

## Server requirements

| Resource | Minimum |
|----------|---------|
| RAM | 8 GB (backend capped at 6 GB) |
| Disk | 20 GB+ (grows with lottery ingestion) |
| OS | Linux x86_64 with Docker Compose v2 |
| Ports | **80** and **443** open to the internet |

## One-time server setup

```bash
# On your web server
git clone https://github.com/ADPortfolioNode/pocketpro_nyl_project.git
cd pocketpro_nyl_project

cp .env.production.example .env
nano .env   # set DOMAIN, ACME_EMAIL, API keys

chmod +x scripts/deploy-production.sh
./scripts/deploy-production.sh
```

Your app will be at **https://your-domain** (after DNS points to the server).

## Deployment modes

### 1. Public HTTPS (demo / marketing)

```env
CADDY_PROFILE=tls
DOMAIN=pocketpro.yourdomain.com
ACME_EMAIL=admin@yourdomain.com
```

```bash
./scripts/deploy-production.sh
```

### 2. Subscriber-only (HTTP basic auth)

Protect the entire app with a username/password (issue credentials to paying customers).

```bash
# Generate password hash (or use the helper script)
chmod +x scripts/generate-subscriber-auth.sh
./scripts/generate-subscriber-auth.sh customer

# Manual alternative:
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'customer-password'
```

```env
CADDY_PROFILE=subscribers
DOMAIN=pocketpro.yourdomain.com
ACME_EMAIL=admin@yourdomain.com
BASIC_AUTH_USER=customer
BASIC_AUTH_HASH=<paste hash from above>
```

```bash
./scripts/deploy-production.sh
```

Customers see a browser login prompt before the app loads.

### 3. Behind your own reverse proxy

If you already run nginx/Apache on the host:

```env
DEPLOY_MODE=direct
FRONTEND_BIND=127.0.0.1
FRONTEND_PORT=3000
```

```bash
DEPLOY_MODE=direct ./scripts/deploy-production.sh
```

Proxy `https://yourdomain` → `http://127.0.0.1:3000` on the host.

## What is exposed

| Service | Public internet | Notes |
|---------|-----------------|-------|
| Caddy (443) | Yes | TLS termination |
| Frontend | No | Internal only; Caddy proxies |
| Backend API | No | Reached via `/api` through frontend nginx |
| ChromaDB | No | Internal only |

## Pin a release version

In `.env`:

```env
POCKETPRO_NYL_REGISTRY=ghcr.io/adportfolionode
POCKETPRO_NYL_VERSION=v1.0.0
```

Use tagged releases from GitHub (`git tag v1.0.0 && git push origin v1.0.0`) so CI publishes images.

Make GHCR packages **public** (GitHub → Packages → mensa-frontend / mensa-backend → Package settings → Change visibility) so your server can `docker pull` without registry login.

### Build on the server (no registry)

If GHCR is private or you deploy straight from a git clone:

```bash
BUILD_LOCAL=1 ./scripts/deploy-production.sh
```

This uses `docker-compose.distribution.build.yml` to compile frontend and backend on the VPS (first run takes several minutes).

## API keys (server-side only)

Set in `.env` on the server — **never** in the frontend image:

- `GEMINI_API_KEY` / `OPENAI_API_KEY` / `GROK_API_KEY` — for AI chat

Ingestion, training, and suggestions work without keys. Chat uses a local fallback when keys are missing.

## Operations

```bash
# Status
docker compose -f docker-compose.distribution.yml --profile tls ps

# Logs
docker compose -f docker-compose.distribution.yml --profile tls logs -f backend

# Update to latest images
docker compose -f docker-compose.distribution.yml --profile tls pull
docker compose -f docker-compose.distribution.yml --profile tls up -d

# Backup subscriber data
docker run --rm -v mensa_backend_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/mensa-data-backup.tar.gz -C /data .
```

## Subscriber workflow

1. You deploy once on your server.
2. Point DNS `mensa.yourdomain.com` → server IP.
3. Issue each subscriber a **basic auth** username/password (or use a single shared credential per tier).
4. Manage billing outside the app (Stripe, PayPal, etc.) — this stack provides access control, not payments.

## Files reference

| File | Purpose |
|------|---------|
| `docker-compose.distribution.yml` | Production stack (images + internal network) |
| `docker-compose.direct.yml` | Optional HTTP publish without Caddy |
| `docker-compose.distribution.build.yml` | Build images on server (no GHCR pull) |
| `.env.production.example` | Production environment template |
| `deploy/caddy/Caddyfile.tls` | HTTPS, no login |
| `deploy/caddy/Caddyfile.subscribers` | HTTPS + basic auth |
| `scripts/deploy-production.sh` | Linux deploy script |
| `scripts/generate-subscriber-auth.sh` | Create basic-auth hash for `.env` |

## Troubleshooting

- **Certificate errors**: Ensure `DOMAIN` DNS resolves to this server and ports 80/443 are open.
- **502 after deploy**: Wait 90s for backend healthcheck; run `docker compose logs backend`.
- **Training OOM**: Reduce `TRAIN_MAX_ATTEMPTS` / `TRAIN_N_ESTIMATORS` in `.env` or raise `BACKEND_MEMORY_LIMIT`.

See also [DOCKER_HUB_DEPLOYMENT.md](./DOCKER_HUB_DEPLOYMENT.md) for publishing images.