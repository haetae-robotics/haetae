#pragma once
#include <algorithm>
#include <array>
#include <gz/math/Pose3.hh>

namespace haetae {
constexpr double kPersonSpeed = 0.24;
using Body = std::array<gz::math::Pose3d, 12>;
inline void advance(Body &current, const Body &target, double dt) {
  if (dt <= 0) return;
  // Appearing at the bay entrance and parking after departure are explicit
  // scene transitions. While present, delayed targets cannot cause a jump.
  const double distance = current[0].Pos().Distance(target[0].Pos());
  const double alpha = current[0].Pos().Z() < 0 || target[0].Pos().Z() < 0 ||
      distance < 1e-12 ? 1.0 : std::min(1.0, kPersonSpeed * dt / distance);
  for (std::size_t i = 0; i < current.size(); ++i) {
    current[i] = gz::math::Pose3d(
        current[i].Pos() + alpha * (target[i].Pos() - current[i].Pos()),
        gz::math::Quaterniond::Slerp(alpha, current[i].Rot(), target[i].Rot()));
  }
}
}  // namespace haetae
