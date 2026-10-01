#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-"$repo/artifacts/gazebo-local"}
mkdir -p "$out"
out=$(cd "$out" && pwd)

docker build -f "$repo/ros/gazebo/Dockerfile" -t haetae-gazebo-live "$repo"
printf 'Starting the container. Open http://127.0.0.1:8765/ after "Live view:" appears below.\n'
docker run --init --rm --cap-add=NET_ADMIN --shm-size=256m -p 127.0.0.1:8765:8765 -p 127.0.0.1:6080:6080 \
  -v "$out:/out" haetae-gazebo-live
