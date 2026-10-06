#pragma once
#include "haetae_arm_guard/permit.hpp"
#include <cmath>
#include <geometry_msgs/msg/twist_stamped.hpp>
#include <trajectory_msgs/msg/joint_trajectory.hpp>

namespace haetae_arm_guard
{
template<class Stamp> uint64_t stamp_ns(const Stamp & stamp)
{
  if (stamp.sec < 0 || stamp.nanosec >= 1000000000) {throw std::invalid_argument("timestamp");}
  return static_cast<uint64_t>(stamp.sec) * 1000000000 + stamp.nanosec;
}
inline std::string base_digest(const geometry_msgs::msg::TwistStamped & command)
{
  const auto & t = command.twist;
  if (t.linear.y != 0 || t.linear.z != 0 || t.angular.x != 0 || t.angular.y != 0 ||
    !std::isfinite(t.linear.x) || !std::isfinite(t.angular.z)) {throw std::invalid_argument("base payload");}
  std::string raw("base\0", 5); append_u64(raw, stamp_ns(command.header.stamp));
  for (double v : {t.linear.x, t.linear.y, t.linear.z, t.angular.x, t.angular.y, t.angular.z}) {
    append_double(raw, v);
  }
  return sha256(raw);
}
inline std::string arm_digest(const trajectory_msgs::msg::JointTrajectory & trajectory)
{
  if (trajectory.joint_names != std::vector<std::string>{"joint1", "joint2", "joint3", "joint4"} ||
    trajectory.points.empty() || trajectory.points.size() > 16) {throw std::invalid_argument("arm joints/points");}
  std::string raw("arm\0", 4); append_u64(raw, stamp_ns(trajectory.header.stamp));
  append_u64(raw, trajectory.joint_names.size(), 4);
  for (const auto & name : trajectory.joint_names) {append_u64(raw, name.size(), 4); raw += name;}
  append_u64(raw, trajectory.points.size(), 4);
  uint64_t last = 0;
  bool first = true;
  for (const auto & point : trajectory.points) {
    auto duration = stamp_ns(point.time_from_start);
    if (point.positions.size() != 4 || !point.velocities.empty() || !point.accelerations.empty() ||
      !point.effort.empty() || (!first && duration <= last) || duration > 1000000000) {
      throw std::invalid_argument("arm point");
    }
    append_u64(raw, duration);
    for (auto v : point.positions) {
      if (!std::isfinite(v)) {throw std::invalid_argument("arm finite");} append_double(raw, v);
    }
    last = duration;
    first = false;
  }
  return sha256(raw);
}
}  // namespace haetae_arm_guard
