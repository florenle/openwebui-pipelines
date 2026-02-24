#!/bin/bash
# build.sh
# Deploys lfbrain pipeline code into the pipelines container.

SRC_DIR="/home/florenle/x/dev/openwebui/lfbrain-pipeline"
CONTAINER="pipelines"

echo "Cleaning up container..."
docker exec $CONTAINER rm -rf /app/pipelines/__pycache__ /app/pipelines/lfbrain* /app/pipelines/failed /app/pipelines/lfutils

echo "Injecting code..."
docker cp $SRC_DIR/lfbrain.py $CONTAINER:/app/pipelines/lfbrain.py
docker cp $SRC_DIR/lfutils $CONTAINER:/app/pipelines/

echo "Restarting $CONTAINER..."
docker restart $CONTAINER
echo "Done. Check logs with: docker logs --tail 30 $CONTAINER"
