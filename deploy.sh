# 1. Variables
SRC_DIR="/home/florenle/x/dev/openwebui/src"
CONTAINER="pipelines"

# 2. Cleanup (Quietly ignore if files don't exist)
echo "Cleaning up container..."
docker exec $CONTAINER rm -rf /app/pipelines/__pycache__ /app/pipelines/lfbrain1* /app/pipelines/failed /app/pipelines/lfutils

# 3. Inject Fresh Code
echo "Injecting code..."
docker cp $SRC_DIR/lfbrain1.py $CONTAINER:/app/pipelines/lfbrain1.py
# Note: docker cp is recursive by default, no -r needed
docker cp $SRC_DIR/lfutils $CONTAINER:/app/pipelines/

# 4. Restart and monitor
echo "Restarting $CONTAINER..."
docker restart $CONTAINER
docker logs -f --tail 30 $CONTAINER