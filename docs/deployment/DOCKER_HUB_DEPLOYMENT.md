# Docker Hub Deployment Guide

This guide provides step-by-step instructions for deploying PocketPro:NYL Predictive RAG to Docker Hub and running it with a single command.

## Prerequisites

- Docker Hub account
- Docker installed locally
- Access to the repository

## Setup Docker Hub Secrets

### GitHub Actions Setup

1. Go to your GitHub repository Settings → Secrets and variables → Actions
2. Add the following secrets:
   - `DOCKER_USERNAME`: Your Docker Hub username
   - `DOCKER_PASSWORD`: Your Docker Hub access token (not password)

### Create Docker Hub Access Token

1. Log in to Docker Hub
2. Go to Account Settings → Security → New Access Token
3. Generate a token with Read, Write, Delete permissions
4. Copy the token and add it as `DOCKER_PASSWORD` in GitHub secrets

## Manual Deployment

### Build and Push Locally

```bash
# Login to Docker Hub
docker login

# Build frontend
docker compose build frontend
docker tag pocketpro_nyl_frontend:latest yourusername/pocketpro-nyl-frontend:latest
docker tag pocketpro_nyl_frontend:latest yourusername/pocketpro-nyl-frontend:v1.0.0

# Build backend
docker compose build backend
docker tag pocketpro_nyl_backend:latest yourusername/pocketpro-nyl-backend:latest
docker tag pocketpro_nyl_backend:latest yourusername/pocketpro-nyl-backend:v1.0.0

# Push images
docker push yourusername/pocketpro-nyl-frontend:latest
docker push yourusername/pocketpro-nyl-frontend:v1.0.0
docker push yourusername/pocketpro-nyl-backend:latest
docker push yourusername/pocketpro-nyl-backend:v1.0.0
```

### Automated Deployment via GitHub Actions

1. Create a new tag to trigger automatic Docker Hub push:
```bash
git tag v1.0.0
git push origin v1.0.0
```

2. The GitHub Actions workflow will:
   - Build both frontend and backend images
   - Push to GitHub Container Registry (GHCR)
   - Test the containers
   - Push to Docker Hub (only on version tags)

## One-Click Run Instructions

### Run Frontend Only

```bash
docker run -d \
  --name mensa-frontend \
  -p 3000:80 \
  -e REACT_APP_API_BASE=http://your-backend-host:5000 \
  yourusername/mensa-frontend:latest
```

### Run Backend Only

```bash
docker run -d \
  --name mensa-backend \
  -p 5000:5000 \
  -e DATA_DIR=/data \
  -e CHROMA_HOST=your-chroma-host \
  -e CHROMA_PORT=8000 \
  -e GEMINI_API_KEY=your_api_key \
  -e CHAT_GPT_API_KEY=your_api_key \
  -e OPENAI_API_KEY=your_api_key \
  -e GROK_API_KEY=your_api_key \
  -v mensa-data:/data \
  yourusername/mensa-backend:latest
```

### Run Full Stack with Docker Compose

Create a `docker-compose-hub.yml` file:

```yaml
services:
  backend:
    image: yourusername/mensa-backend:latest
    container_name: mensa_backend
    ports:
      - "5000:5000"
    environment:
      - DATA_DIR=/data
      - CHROMA_HOST=mensa_chroma
      - CHROMA_PORT=8000
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - CHAT_GPT_API_KEY=${CHAT_GPT_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - GROK_API_KEY=${GROK_API_KEY}
    volumes:
      - backend_data:/data
    depends_on:
      chroma:
        condition: service_healthy
    restart: unless-stopped

  frontend:
    image: yourusername/mensa-frontend:latest
    container_name: mensa_frontend
    ports:
      - "3000:80"
    environment:
      - REACT_APP_API_BASE=http://localhost:5000
    depends_on:
      - backend
    restart: unless-stopped

  chroma:
    image: chromadb/chroma:0.5.3
    container_name: mensa_chroma
    environment:
      - IS_PERSISTENT=TRUE
      - ANONYMIZED_TELEMETRY=FALSE
    ports:
      - "8000:8000"
    volumes:
      - chroma_data:/chroma/chroma
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/heartbeat"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: on-failure

volumes:
  backend_data:
  chroma_data:
```

Run with:

```bash
# Copy environment template
cp .env.example .env
# Edit .env with your API keys

# Start services
docker compose -f docker-compose-hub.yml up -d

# View logs
docker compose -f docker-compose-hub.yml logs -f
```

## Version Management

### Semantic Versioning

Use semantic versioning for tags:
- `v1.0.0` - Major release
- `v1.1.0` - Minor release (new features)
- `v1.1.1` - Patch release (bug fixes)

### Tagging Workflow

```bash
# Create annotated tag
git tag -a v1.0.0 -m "Release v1.0.0: Production-ready Docker setup"

# Push tag
git push origin v1.0.0

# This triggers GitHub Actions to build and push to Docker Hub
```

## Image Sizes

Expected image sizes (approximate):
- Frontend (nginx): ~50-100 MB
- Backend (Python): ~500-800 MB
- ChromaDB: ~200-300 MB

## Security Best Practices

1. **Use access tokens, not passwords** for Docker Hub authentication
2. **Scan images for vulnerabilities** before pushing:
   ```bash
   docker scan yourusername/mensa-frontend:latest
   docker scan yourusername/mensa-backend:latest
   ```
3. **Use specific version tags** in production, not `latest`
4. **Enable content trust**:
   ```bash
   export DOCKER_CONTENT_TRUST=1
   docker push yourusername/mensa-frontend:v1.0.0
   ```
5. **Regularly update base images** to get security patches

## Troubleshooting

### Authentication Issues

```bash
# Logout and login again
docker logout
docker login
```

### Push Failures

```bash
# Check disk space
docker system df

# Clean up if needed
docker system prune -a
```

### Image Not Found

```bash
# Verify image exists locally
docker images | grep mensa

# Pull from Docker Hub
docker pull yourusername/mensa-frontend:latest
```

## CI/CD Integration

The GitHub Actions workflow (`.github/workflows/docker-build-push.yml`) handles:
- Multi-platform builds (linux/amd64, linux/arm64)
- Automated testing
- GHCR and Docker Hub pushes
- Version tagging

To enable Docker Hub pushes, ensure secrets are configured in GitHub repository settings.
