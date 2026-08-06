# Mensa Project - Operations & Maintenance Guide

**Version:** 1.0  
**Date:** January 16, 2026  
**Status:** Production Ready

---

## Quick Start Operations

### Start the Application
```bash
cd "e:/2024 RESET/mensa_project"

# Option 1: Fast startup (recommended for daily use)
./start.sh

# Option 2: Detailed monitoring (first-time / troubleshooting)
./monitor_startup.sh

# Option 3: Manual docker-compose (if scripts fail)
docker-compose down -v
docker-compose up -d --build
```

### Check Status
```bash
# All containers
docker-compose ps

# Backend health
curl http://127.0.0.1:5000/api

# Ingestion progress
curl http://127.0.0.1:5000/api/startup_status | jq .

# View logs in real-time
docker-compose logs -f
```

### Access Application
```
Frontend:  http://localhost:3000
Backend:   http://localhost:5000/api
Chroma DB: http://localhost:8000/api/v1/heartbeat
```

---

## Normal Operations

### Daily Startup
```bash
./start.sh
# Wait ~1 minute for all services healthy
# Frontend accessible at http://localhost:3000
```

### During Development
```bash
# After code changes, rebuild and restart
docker-compose up -d --build

# Watch the build and startup
docker-compose logs -f backend

# When you see "Uvicorn running on 0.0.0.0:5000", it's ready
```

### Code Changes Only
```bash
# If you only changed .py files (no requirements.txt)
docker-compose up -d --build

# Expected: 2-4 minute rebuild (fast, uses cache)
# Multi-stage build caches pip dependencies
```

### Dependency Changes
```bash
# If you modified requirements.txt
docker-compose up -d --build

# Expected: 10-15 minute rebuild (rebuilds all packages)
# Still faster than pre-optimization due to timeouts and retries
```

---

## Monitoring During Operation

### Watch Startup Progress
```bash
# Terminal 1: Follow logs
docker-compose logs -f backend

# Terminal 2: Check API health
watch -n 2 'curl -s http://127.0.0.1:5000/api/startup_status | jq .'
```

### Expected Log Output
```
[2026-01-16 14:23:45] Starting auto-ingestion for all configured games...
[2026-01-16 14:23:46] [1/8] Ingesting take5...
[2026-01-16 14:24:01] ✓ take5 ingested successfully
[2026-01-16 14:24:02] [2/8] Ingesting pick3...
...
[2026-01-16 14:25:15] ✓ Background ingestion completed in 89.3s
```

### Check Individual Services
```bash
# Chroma (vector database)
curl -I http://127.0.0.1:8000/api/v1/heartbeat
# Expected: HTTP/1.1 200 OK

# Backend (FastAPI)
curl -I http://127.0.0.1:5000/api
# Expected: HTTP/1.1 200 OK

# Frontend (React)
curl -I http://127.0.0.1:3000
# Expected: HTTP/1.1 200 OK
```

---

## Troubleshooting Guide

### Problem: Services Stuck in "Starting"
**Solution:**
```bash
# Check health status
docker-compose ps

# View logs
docker-compose logs backend
docker-compose logs frontend

# If persists, restart
docker-compose restart

# If still stuck, rebuild
docker-compose down
docker-compose up -d --build
```

### Problem: 404 on Game Endpoints
**Cause:** Game data still ingesting  
**Solution:** Wait 2-3 minutes, check progress:
```bash
curl http://127.0.0.1:5000/api/startup_status | jq '.current_game, .progress'

# Once progress shows 8/8, all games ready
```

### Problem: Network Error Connecting to Backend
**Cause:** Backend not yet healthy or not listening  
**Solution:**
```bash
# Check if backend container is running
docker-compose ps

# Check backend logs for startup errors
docker-compose logs backend

# Verify port 5000 is accessible
netstat -an | grep 5000

# Restart backend
docker-compose restart backend
```

### Problem: Build Fails with Timeout
**Cause:** Slow network connection to PyPI  
**Solution:** Already handled! Dockerfile has:
- 300 second timeout (5 minutes per package)
- 5 automatic retries per failed download

Just run again:
```bash
docker-compose up -d --build
# Docker remembers where it failed and resumes
```

### Problem: "Cannot allocate memory"
**Cause:** httptools C extension (FIXED in this version)  
**Solution:** Already resolved by using h11 instead
```bash
# Just restart, should work now
docker-compose down
docker-compose up -d --build
```

### Problem: Volumes or Data Issues
**Clear and restart:**
```bash
# Remove everything and start fresh
docker-compose down -v
docker system prune -a -f

# Rebuild from scratch
docker-compose up -d --build
```

---

## Performance Monitoring

### Measure Startup Performance
```bash
# Using the monitor script (recommended)
time ./monitor_startup.sh

# Or manually
time docker-compose up -d --build
```

### Expected Times
```
Docker build (first):      ~12 min
Docker build (code only):  ~2-4 min
Services healthy:          ~60 sec
Backend responsive:        ~30-60 sec
Frontend visible:          ~30-40 sec
Full data ready:           ~3-4 min total
```

### Performance Regression Detection
If startup takes significantly longer than expected:
```bash
# Check layer cache reuse
docker system df

# View build cache
docker buildx du

# If cache is low, manually prune and rebuild
docker system prune -a
docker-compose up -d --build
```

---

## Maintenance Tasks

### Weekly
- [ ] Check container memory usage: `docker stats`
- [ ] Review error logs: `docker-compose logs | grep -i error`
- [ ] Verify all games ingesting: `curl /api/startup_status`

### Monthly
- [ ] Clean up old images: `docker image prune`
- [ ] Verify backup/data integrity: `ls -lh ./data/chroma/`
- [ ] Check Docker disk usage: `docker system df`

### Quarterly
- [ ] Review ingestion performance trends
- [ ] Check for Python package updates
- [ ] Validate game data freshness (if applicable)

---

## Backup & Recovery

### Backup ChromaDB Data
```bash
# Copy data directory
cp -r ./data/chroma ./backup/chroma_$(date +%Y%m%d).backup

# Or archive it
tar -czf chroma_backup_$(date +%Y%m%d).tar.gz ./data/chroma/
```

### Restore from Backup
```bash
# Stop services
docker-compose down

# Restore data
rm -rf ./data/chroma
cp -r ./backup/chroma_YYYYMMDD.backup ./data/chroma

# Restart
docker-compose up -d
```

### Clear Data and Reingest
```bash
# Stop services
docker-compose down -v

# Restart (will trigger fresh ingestion)
docker-compose up -d --build

# Monitor progress
curl http://127.0.0.1:5000/api/startup_status | jq .
```

---

## Scaling (Advanced)

### Increase Memory Limit
Edit `docker-compose.yml`:
```yaml
backend:
  deploy:
    resources:
      limits:
        memory: 8G  # Changed from 4G
```

Then restart:
```bash
docker-compose up -d
```

### Add More Parallel Ingestions
Edit `backend/config.py`:
```python
MAX_CONCURRENT_CLONES = 5  # Was 3
```

Rebuild:
```bash
docker-compose up -d --build
```

---

## Emergency Procedures

### Emergency Stop
```bash
# Stop all containers immediately
docker-compose down

# Or force kill
docker-compose kill
```

### Recover from Corruption
```bash
# Remove corrupted data
docker-compose down -v

# Rebuild everything from scratch
docker-compose up -d --build

# System is fully restored
```

### Rollback to Previous Code
```bash
# If something broke after deploy
git log --oneline -5

# Revert to previous commit
git revert HEAD
docker-compose down
docker-compose up -d --build
```

---

## Optimization Tips

### Speed Up Rebuilds
```bash
# Use BuildKit for faster builds (if available)
export DOCKER_BUILDKIT=1
docker-compose up -d --build
```

### Reduce Image Size
```bash
# Current optimized size: ~1.8 GB
# Multi-stage build already applied
# Further optimization: Pin more dependencies to pre-built wheels
```

### Cache Local PyPI Packages
```bash
# For production environments with limited internet:
# Pre-download wheels and use local pip cache
# See OPTIMIZATION_REPORT.md for details
```

---

## Useful Commands Reference

```bash
# Status
docker-compose ps
docker-compose logs -f [service]

# Start/Stop
docker-compose up -d --build
docker-compose down
docker-compose restart [service]

# Cleanup
docker system prune -a
docker volume prune

# Testing
curl http://127.0.0.1:5000/api
curl http://127.0.0.1:5000/api/startup_status | jq .
curl http://127.0.0.1:3000

# Monitoring
docker stats
docker-compose logs --tail=100

# Development
docker-compose logs -f backend
docker exec -it mensa_backend /bin/bash
```

---

## Key Resources

| Document | Purpose |
|----------|---------|
| [OPTIMIZATION_QUICK_REFERENCE.md](OPTIMIZATION_QUICK_REFERENCE.md) | Daily user guide |
| [OPTIMIZATION_REPORT.md](OPTIMIZATION_REPORT.md) | Technical details |
| [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) | Deployment steps |
| [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) | System architecture |
| [monitor_startup.sh](monitor_startup.sh) | Startup monitoring |
| [start.sh](start.sh) | Quick start script |

---

## Support & Documentation

All changes are fully documented:
- **Code comments** in modified files
- **Inline documentation** in scripts
- **Multiple reference guides** for different use cases
- **Troubleshooting guide** above for common issues

**For questions:**
1. Check [OPTIMIZATION_QUICK_REFERENCE.md](OPTIMIZATION_QUICK_REFERENCE.md) first
2. Review [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) for understanding
3. Consult [OPTIMIZATION_REPORT.md](OPTIMIZATION_REPORT.md) for technical details
4. Check logs: `docker-compose logs -f`

---

## Conclusion

The Mensa project is now optimized for:
- ✅ **Fast startup** (30-40 sec to UI)
- ✅ **Fast iteration** (2-4 min rebuilds)
- ✅ **Reliability** (health check-driven)
- ✅ **Observability** (state tracking & monitoring)
- ✅ **Maintainability** (clean code & documentation)

All operations follow industry best practices with comprehensive documentation for every scenario.

**Status:** Production Ready - Deploy with confidence.
