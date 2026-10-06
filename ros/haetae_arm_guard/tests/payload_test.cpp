#include "haetae_arm_guard/payload.hpp"
#include <cstdlib>
#include <iostream>

int main()
{
  geometry_msgs::msg::TwistStamped base;
  base.header.stamp.sec = 1; base.twist.linear.x = .15; base.twist.angular.z = .2;
  if (haetae_arm_guard::base_digest(base) !=
    "258d4f3f62b83e2c144be7d2fc926faf92000525bdc1a305f9b674eea4c93fdb") {return 1;}
  trajectory_msgs::msg::JointTrajectory arm;
  arm.header.stamp.sec = 1; arm.joint_names = {"joint1", "joint2", "joint3", "joint4"};
  trajectory_msgs::msg::JointTrajectoryPoint first, last;
  first.positions = {0, -1, .7, .3}; last.positions = {.3, -1, .7, .3}; last.time_from_start.sec = 1;
  arm.points = {first, last};
  if (haetae_arm_guard::arm_digest(arm) !=
    "0b836ee3a5aae545c294e357ce857c032f686bebc673294f90be526c41a04e83") {return 1;}
  for (auto & point : arm.points) {
    for (auto & value : point.positions) {
      value += .001;
      if (haetae_arm_guard::arm_digest(arm) ==
        "0b836ee3a5aae545c294e357ce857c032f686bebc673294f90be526c41a04e83") {return 1;}
      value -= .001;
    }
  }
  std::cout << "Python/C++ exact payload vectors passed\n";
  arm.header.stamp.sec = 0;
  if (haetae_arm_guard::arm_digest(arm) !=
    "2ffe617c47e93dde70c78ad1f7afbed8981a22b955ede4eb85c85ea4f4e3b64c") {return 1;}
}
