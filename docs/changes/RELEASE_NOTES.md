# Mensa Project - Release Notes

## [Unreleased]

### Added
- Production-grade Docker setup with multi-stage builds, non-root users, and health checks
- Rate limiting middleware on backend (100 req/min per IP) and frontend nginx (30 req/s)
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options, Permissions-Policy, Referrer-Policy
- GitHub Actions CI/CD workflow for automated Docker builds and pushes to GHCR and Docker Hub
- Automatic GitHub release drafting on version tags
- Docker Hub distribution compose file (`docker-compose.hub.yml`)
- Production verification script (`verify_production.ps1`)
- Comprehensive RELEASE_NOTES.md template
- `.env.example` with all documented environment variables

### Changed
- `frontend/nginx.conf`: Added rate limiting zones, security headers, static file caching, hidden file blocking
- `backend/main.py`: Added in-memory rate limiter middleware (skips health endpoint)
- Enhanced error handling in chat endpoint with structured fallback responses

### Security
- Non-root user for both frontend (nginx) and backend (appuser) containers
- server_tokens off to hide nginx version
- Denied access to hidden files (.env, .git, package.json) via nginx
- Backend rate limiting to prevent abuse
- HSTS with preload enabled

### Infrastructure
- CI/CD pipeline: build → test → push → release on tag
- Multi-registry push: GitHub Container Registry + Docker Hub
- Layer caching with GitHub Actions cache and BuildKit
- Docker Hub compose file for one-click distribution

---

## Versioning

This project uses [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes to API or data format
- **MINOR**: New features (new games, AI providers, endpoints)
- **PATCH**: Bug fixes, security patches, performance improvements

### Creating a Release

```bash
# 1. Update this file with changes
# 2. Commit and tag
git add RELEASE_NOTES.md
git commit -m "chore: prepare release v1.0.0"
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin main --tags
```

The CI pipeline will automatically build, push to registries, and create a GitHub Release.
