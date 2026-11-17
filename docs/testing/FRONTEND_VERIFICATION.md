# Frontend Verification Checklist

## System Status
- **Frontend**: http://localhost:3000
- **Backend**: http://localhost:5000
- **ChromaDB**: http://localhost:8000
- **API Proxy**: Nginx routing `/api/*` to backend

## Installation & Build Status
✅ Frontend rebuilt with fixed `apiBase.js` 
✅ API base set to empty string (returns relative paths)
✅ Nginx config deployed with `/api/` proxy routing
✅ All containers rebuilt with `--build` flag

## Browser Verification Steps

### 1. Initial Page Load
□ Open http://localhost:3000 in browser
□ Dashboard should load without network errors
□ Check browser console (F12) - should have NO red errors about `/api/api/*` paths
□ Page title should be "Mensa Project"

### 2. Top Navigation
□ "Mensa Project" header visible
□ Status indicator should be visible
□ No "Error" badges or warnings

### 3. Game Selection  
□ "Select a game" dropdown is populated
□ All 8 games visible:
  - take5
  - pick3
  - powerball
  - megamillions
  - pick10
  - cash4life
  - quickdraw
  - nylotto
□ No "undefined" or "loading..." states

### 4. ChromaDB Collections Status Panel
□ Panel loads without "Loading..." message
□ Shows collection count per game
□ No timeout errors in console

### 5. Data Initialization Button
□ "Start Initialization" button is visible and enabled
□ Button status shows current state (ready/pending/complete)
□ Per-game status indicators visible

### 6. Startup Progress
□ Progress bar shows 0/8 initially
□ Status shows "ready"
□ Click "Start Initialization" to begin ingestion
□ Progress should advance as games are fetched

### 7. API Endpoint Tests (in Browser Console)
```javascript
// Test /api/health
fetch('/api/health').then(r => r.json()).then(console.log)

// Test /api/games  
fetch('/api/games').then(r => r.json()).then(console.log)

// Test /api/startup_status
fetch('/api/startup_status').then(r => r.json()).then(console.log)

// Test /api/chroma/collections
fetch('/api/chroma/collections').then(r => r.json()).then(console.log)
```

Expected responses:
- `/api/health`: `{"status":"healthy","timestamp":...}`
- `/api/games`: Array of game names
- `/api/startup_status`: State object with progress
- `/api/chroma/collections`: Collections status

## Common Issues & Troubleshooting

### Issue: Still seeing `/api/api/*` in Network tab
**Solution**: Hard refresh with Ctrl+Shift+R (or Cmd+Shift+R on Mac)

### Issue: 504 Gateway Timeout
**Solution**: Backend may be starting. Wait 30 seconds and refresh.

### Issue: "Cannot GET /" 
**Solution**: Nginx config issue. Run:
```bash
docker exec mensa_frontend nginx -s reload
```

### Issue: CORS errors
**Solution**: Backend and frontend are on same host (localhost:3000). Should not have CORS issues.

## Success Indicators

✅ Dashboard fully loads
✅ All 8 games appear in dropdown
✅ ChromaDB panel shows collections
✅ Initialization button is clickable
✅ Network tab shows `/api/startup_status`, `/api/games`, etc. (NOT `/api/api/*`)
✅ Console has no red error messages
✅ Clicking "Start Initialization" begins data ingestion
✅ Progress bar updates in real-time

## Next Steps After Verification

1. **If all checks pass**:
   - System is ready for data ingestion
   - Click "Start Initialization" to begin
   - Wait for progress bar to reach 100%
   - Estimated time: 5-10 minutes depending on data size

2. **If issues remain**:
   - Screenshot the browser console errors
   - Check `docker logs mensa_backend --tail 20`
   - Check `docker logs mensa_frontend --tail 20`
   - Report specific error messages

## Key Files Modified

- `/frontend/.env.production` → `REACT_APP_API_BASE=` (empty)
- `/frontend/src/utils/apiBase.js` → Returns empty string, not localhost:5000
- `/frontend/nginx.conf` → Proxy `/api/*` to backend:5000
- `/compose.yaml` → All services mapped and networked correctly

## Container Info

```bash
# Check container status
docker ps --filter name=mensa

# View logs
docker logs mensa_frontend
docker logs mensa_backend  
docker logs mensa_chroma

# Restart if needed
docker restart mensa_backend
docker restart mensa_frontend
```

Date Built: 2026-02-06
API Base: Empty (relative proxy)
Frontend Port: 3000
Backend Port: 5000
Chroma Port: 8000
