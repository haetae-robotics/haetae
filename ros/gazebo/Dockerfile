FROM rust:1.97-bookworm AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
RUN cargo build --release --locked -p haetae

FROM ros:jazzy
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-cryptography ros-jazzy-rmw-fastrtps-cpp \
    xvfb x11vnc openbox novnc websockify libgl1-mesa-dri \
    ros-jazzy-ros-gz ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers \
    ros-jazzy-controller-manager ros-jazzy-robot-state-publisher ros-jazzy-xacro \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY ros/ ./ros/
COPY sim/ ./sim/
COPY tools/build_reference_visuals.py tools/build_product_visuals.py ./tools/
RUN . /opt/ros/jazzy/setup.sh && python3 tools/build_product_visuals.py
COPY --from=builder /src/target/release/haetae ./bin/haetae
ENV ROS_DOMAIN_ID=81 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
EXPOSE 8765 6080
CMD ["/bin/bash", "ros/gazebo/run_gui_docker.sh"]
