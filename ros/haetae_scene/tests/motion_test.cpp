#include "motion.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}
int main() {
  haetae::Body current{}, target{};
  for (std::size_t i = 0; i < current.size(); ++i) {
    current[i] = gz::math::Pose3d(5, 6, 1.16 + i * .01, 0, 0, 0);
    target[i] = gz::math::Pose3d(5, 9, 1.16 + i * .01, 0, 0, 1.5);
  }
  auto old = current;
  haetae::advance(current, target, 0);
  require(current == old, "paused scene moved");
  for (double dt : {.001, .017, .1}) {
    old = current;
    haetae::advance(current, target, dt);
    require(current[0].Pos().Distance(old[0].Pos()) <= .24 * dt + 1e-12,
            "delayed target caused a root jump");
    require(std::abs(current[11].Pos().Z() - current[0].Pos().Z() - .11) < 1e-12,
            "body lost its shared root");
    require(std::abs(current[0].Rot().Dot(current[0].Rot()) - 1) < 1e-12,
            "interpolation produced a non-unit quaternion");
  }
  target = current;
  target[0].Pos().Y() += .0001;
  haetae::advance(current, target, .01);
  require(current[0] == target[0], "short step overshot target");
  for (auto &pose : target) pose.Pos().Z() = -5;
  haetae::advance(current, target, .001);
  require(current == target, "departure did not park geometry");
  for (auto &pose : target) pose.Pos().Z() = 1.16;
  haetae::advance(current, target, .001);
  require(current == target, "entrance did not place geometry");
  std::cout << "native person motion checks passed\n";
}
