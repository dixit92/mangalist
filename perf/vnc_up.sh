#!/bin/sh
# A throwaway copy of the owner's container (release image, its VNC stack), copied database, library read-only,
# no network, no headless jobs. Removed with: docker rm -f perf-vnc
docker run -d --name perf-vnc --network none \
  -v /mnt/user/appdata/mangalist:/src:ro -v /mnt/user:/data:ro \
  --tmpfs /config:rw,size=1g,mode=0777 \
  -e TZ=America/New_York -e MANGALIST_DOWNLOADS=1 -e MANGALIST_HEADLESS=0 -e SECURE_CONNECTION=0 \
  --entrypoint sh mangalist:2026.10.31 -c 'cp /src/mangalist.db /config/ && chown 99:100 /config /config/mangalist.db && exec /init'
