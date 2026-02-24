#!/bin/bash
# start-all.sh
# Deploys and starts the full lfbrain stack.
# Usage: cd ~/x/dev/openwebui && ./lfbrain-pipeline/start-all.sh

set -e

ROOT="/home/florenle/x/dev/openwebui"

# Step 2: Build my-open-webui
read -t 10 -p "Build my-open-webui? [y/N]: " answer
if [[ "$answer" == "y" || "$answer" == "Y" ]];
then
    echo "=== Building my-open-webui ==="
    cd $ROOT/open-webui
    docker build -t my-open-webui:latest .
fi

# Step 4: Build lfbrain-orchestrator
echo "=== Building lfbrain-orchestrator ==="
cd $ROOT/lfbrain-orchestrator
./build.sh

# Step 5: Start all services
echo "=== Starting all services ==="
cd $ROOT/lfbrain-pipeline
# docker rm -f lfbrain-orchestrator pipelines open-webui 2>/dev/null || true
docker compose down
docker compose up -d

# Step 3: Deploy lfbrain-pipeline
echo "=== Deploying lfbrain-pipeline ==="
cd $ROOT/lfbrain-pipeline
./build.sh

# Diagnostics
echo ""
echo "=== Diagnostics ==="

echo "-- Podman toolbox container --"
if podman ps | grep -q "llama-vulkan-radv"; then
    echo "llama-vulkan-radv container: UP"
else
    echo "llama-vulkan-radv container: DOWN"
fi

echo "-- llama_server --"
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "llama_server: RUNNING on port 8080"
else
    echo "llama_server: NOT RUNNING"
    echo ""
    echo "To start llama_server:"
    echo "  1. Open a dedicated terminal"
    echo "  2. toolbox enter llama-vulkan-radv"
    echo "  3. llama-server -m ~/models/gpt-oss-20b/gpt-oss-20b-Q4_K_M.gguf -ngl 999 --host 0.0.0.0 --port 8080 -c 32768"
fi

echo ""
echo "=== Done ==="
