#!/usr/bin/env bash
set -eo pipefail
# ROS setup scripts read optional variables before initializing them.
source /opt/ros/jazzy/setup.bash
source /opt/haetae/setup.bash
set -u

export DISPLAY=:99
export QT_QPA_PLATFORM=xcb
export QT_X11_NO_MITSHM=1
export LIBGL_ALWAYS_SOFTWARE=1

Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp > /out/xvfb.log 2>&1 &
xvfb_pid=$!
for attempt in {1..100}; do
  if [[ -S /tmp/.X11-unix/X99 ]]; then break; fi
  sleep 0.1
done
if [[ ! -S /tmp/.X11-unix/X99 ]]; then
  echo 'Xvfb did not start' >&2
  exit 1
fi
openbox > /out/openbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -localhost -nopw -rfbport 5900 \
  > /out/x11vnc.log 2>&1 &
x11vnc_pid=$!
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 \
  > /out/websockify.log 2>&1 &
websockify_pid=$!
sleep 0.5
for pid in "$xvfb_pid" "$x11vnc_pid" "$websockify_pid"; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo 'Gazebo desktop relay did not start; inspect /out/*.log' >&2
    exit 1
  fi
done

if [[ ${HAETAE_DEMO_PROFILE:-reference} == household ]]; then
  exec python3 ros/gazebo/run_reference.py bin/haetae --household-hazards \
    --live-port 8765 --live-bind 0.0.0.0 --gazebo-gui --wait-for-viewer \
    --manual-start --step-through --secure-graph --live-hold-seconds 3600 --out /out
fi
exec python3 ros/gazebo/run_reference.py bin/haetae \
  --live-port 8765 --live-bind 0.0.0.0 --gazebo-gui \
  --wait-for-viewer --manual-start --step-through --attack-probes --secure-graph --compound-repeat 6 \
  --live-hold-seconds 3600 --out /out
