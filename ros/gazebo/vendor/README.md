# Pinned product description sources

The live reference uses **Husarion ROSbot XL, standard rubber wheels, with
ROBOTIS OpenMANIPULATOR-X**. This is a manufacturer-described mobile manipulator,
not a Haetae-designed robot or a manufacturer endorsement.

`manifest.json` records repositories, immutable commits and SHA-256 hashes of
every copied source. Files within `rosbot_ros/` and `open_manipulator/` are
unmodified upstream files, with their Apache-2.0 licenses and package attribution.
The Husarion arm macro is their integration of the ROBOTIS manipulator description.

Sources:

- https://github.com/husarion/rosbot_ros/tree/ff77c662bdfd1fac933af74df488c917f987afc5
- https://github.com/ROBOTIS-GIT/open_manipulator/tree/9f84095404d3e596267cd520e90963011059ebf6
- https://husarion.com/manuals/rosbot-xl/

Haetae adaptations live outside those source directories:

- `../rosbot_xl.urdf.xacro` composes the official body, wheels, antenna and arm
  macros with the official mounting pose and Home joint positions. It adds one
  Gazebo ros2_control system with Haetae's controller names and timeout settings.
  The simulator position gain is 0.5 (100 Hz manager); this is simulator tuning,
  not a measured real Dynamixel response. The gate keeps its 0.05 rad tracking
  tolerance and one-second trajectory limit.
  Separate upstream drive/arm managers, firmware drivers, navigation, MoveIt,
  IMU and optional lidar/camera components are not launched.
- `tools/build_product_visuals.py` converts the same expanded URDF, DAE and STL
  visuals to a local browser asset, preserving link transforms, materials, joint
  axes and mimic joints. No invented shell or animated odometry-based wheels.
- The gripper is held closed by a local controller; grasping is not tested.
  Haetae admits/cancels trajectories for all four arm joints. This simulation
  does not verify real Dynamixel braking, hardware watchdogs or collision planning.

Names and trademarks belong to their owners. No hardware purchase is necessary
to run this simulation. No model assets are fetched at runtime.
