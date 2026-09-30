"""Haetae's simulation settings for the pinned ROSbot XL manipulation kit."""
import math
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4")
HOME = (0.0, -1.0, 0.7, 0.3)
MODEL_NAME = "ROSbot XL + OpenMANIPULATOR-X"
PERSON_DISTANCE_M = 0.65


def arm_policy():
    limits = ((-4 / 5 * math.pi, math.pi), (-math.pi / 2, math.pi / 2),
              (-1.5, 1.4), (-1.7, 1.97))
    return {"joints": [{"name": name, "min_position": lower, "max_position": upper,
                        "max_velocity": 1.0, "max_acceleration": 40.0}
                       for name, (lower, upper) in zip(ARM_JOINTS, limits)],
            "max_points": 8, "max_duration_ms": 1000,
            "max_start_error": 0.01, "max_tracking_error": 0.05, "min_confidence": 0.9}


def resolve_meshes(urdf):
    """Use only vendored local files; leave physical data untouched."""
    robot = ET.fromstring(urdf)
    packages = {"rosbot_description": HERE / "vendor/rosbot_ros/rosbot_description",
                "open_manipulator_description": HERE / "vendor/open_manipulator/open_manipulator_description"}
    for mesh in robot.findall(".//mesh"):
        uri = mesh.get("filename")
        package, relative = uri.removeprefix("package://").split("/", 1)
        source = packages[package] / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        mesh.set("filename", source.resolve().as_uri())
    return ET.tostring(robot, encoding="unicode")
