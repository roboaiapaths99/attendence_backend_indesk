#!/bin/bash

# Deployment script for LogDay Backend on VPS
# Port: 8001
# Volumes: uploads, logs

echo "Moving to backend directory..."
cd /var/www/backend

echo "Pulling latest changes from main branch..."
git pull origin main

echo "Stopping and removing existing container..."
docker stop logday-api && docker rm logday-api

echo "Building new docker image..."
# Fix: Force BuildKit to 0 or comment out if host doesn't support buildx
export DOCKER_BUILDKIT=0
docker build -t logday-backend .

echo "Starting new container..."
docker run -d \
  --name logday-api \
  --restart always \
  -p 8001:8001 \
  --env-file .env \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/logs:/app/logs \
  logday-backend

echo "Deployment complete! Backend is running on port 8001."
