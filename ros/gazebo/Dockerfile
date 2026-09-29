FROM rust:1.97-bookworm AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
RUN cargo build --release --locked -p haetae

FROM ros:jazzy
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-cryptography ros-jazzy-rmw-fastrtps-cpp \
    ros-jazzy-ros-gz ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers \
    ros-jazzy-controller-manager ros-jazzy-robot-state-publisher ros-jazzy-xacro \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY ros/ ./ros/
COPY sim/ ./sim/
COPY --from=builder /src/target/release/haetae ./bin/haetae
ENV ROS_DOMAIN_ID=81 RMW_IMPLEMENTATION=rmw_fastrtps_cpp QT_QPA_PLATFORM=offscreen
EXPOSE 8765
CMD ["/bin/bash", "-lc", "source /opt/ros/jazzy/setup.bash && exec python3 ros/gazebo/run_reference.py bin/haetae --live-port 8765 --live-bind 0.0.0.0 --wait-for-viewer --live-hold-seconds 120 --out /out"]
