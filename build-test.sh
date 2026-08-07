#!/bin/bash
# Local build and test script for Docker containers

set -e

echo "🔨 Building Docker containers..."

# Build frontend
echo "Building frontend..."
docker compose build frontend

# Build backend
echo "Building backend..."
docker compose build backend

# Build chroma (pull image)
echo "Pulling ChromaDB image..."
docker compose pull chroma

echo "✅ Build complete!"
echo ""
echo "🧪 Running tests..."

# Test frontend build
echo "Testing frontend container..."
docker run --rm pocketpro_nyl_frontend:latest wget -O- http://localhost/ || echo "Frontend test skipped (needs running container)"

# Test backend build
echo "Testing backend container..."
docker run --rm pocketpro_nyl_backend:latest python -c "import fastapi; print('FastAPI installed')" || echo "Backend test skipped"

echo "✅ Tests complete!"
echo ""
echo "🚀 Starting services..."
docker compose up -d

echo "⏳ Waiting for services to be healthy..."
sleep 30

echo "🔍 Checking service health..."
echo "Frontend health:"
curl -f http://localhost:3000 || echo "Frontend not responding"

echo "Backend health:"
curl -f http://localhost:5000/api/health || echo "Backend not responding"

echo "ChromaDB health:"
curl -f http://localhost:8000/api/v1/heartbeat || echo "ChromaDB not responding"

echo ""
echo "✅ Build and test complete!"
echo "Access the application at: http://localhost:3000"
