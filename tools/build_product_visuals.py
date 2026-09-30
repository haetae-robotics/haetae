#!/usr/bin/env python3
"""Convert the pinned product URDF/DAE/STL visuals without changing geometry.

Requires xacro only when building. The runtime browser uses the checked-in JSON
and gzip asset. This bounded importer deliberately rejects unsupported DAE
features rather than silently displaying a different model.
"""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "ros/gazebo/vendor"
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


def numbers(value, default="0 0 0"):
    return [float(v) for v in (value if value is not None else default).split()]


def origin(element):
    return {"xyz": numbers(element.get("xyz")) if element is not None else [0, 0, 0],
            "rpy": numbers(element.get("rpy")) if element is not None else [0, 0, 0]}


def mesh_path(uri):
    package, relative = uri.removeprefix("package://").split("/", 1)
    repo = "rosbot_ros" if package == "rosbot_description" else "open_manipulator"
    if package not in ("rosbot_description", "open_manipulator_description"):
        raise ValueError("Unsupported mesh package: " + package)
    return VENDOR / repo / package / relative


def transform(matrix, vector, normal=False):
    w = 0 if normal else 1
    result = [sum(matrix[a * 4 + b] * v for b, v in enumerate([*vector, w])) for a in range(3)]
    if normal:
        length = math.sqrt(sum(v * v for v in result))
        return [v / length for v in result] if length else [0, 0, 1]
    return result


def multiply(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def indexed(triangles):
    positions, normals, indices, seen = [], [], [], {}
    for p, n in triangles:
        key = tuple(round(v, 8) for v in [*p, *n])
        if key not in seen:
            seen[key] = len(positions) // 3
            positions.extend(key[:3]); normals.extend(key[3:])
        indices.append(seen[key])
    return {"positions": positions, "normals": normals, "indices": indices}


def stl(source):
    data = source.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError("Expected pinned binary STL: " + str(source))
    vertices = []
    for i in range(count):
        values = struct.unpack_from("<12f", data, 84 + i * 50)
        vertices.extend((values[offset:offset + 3], values[:3]) for offset in (3, 6, 9))
    return [{"color": [0.2, 0.2, 0.2, 1], **indexed(vertices)}]


def collada(source):
    root = ET.parse(source).getroot()
    if root.findtext("c:asset/c:up_axis", namespaces=NS) != "Z_UP":
        raise ValueError("Expected pinned Z_UP COLLADA")
    if root.findall(".//c:texture", NS) or root.findall(".//c:controller", NS):
        raise ValueError("Textures/skinning not supported by this product importer")
    effects = {e.get("id"): numbers(e.findtext(".//c:diffuse/c:color", namespaces=NS), "0.2 0.2 0.2 1")
               for e in root.findall("c:library_effects/c:effect", NS)}
    materials = {m.get("id"): effects[m.find("c:instance_effect", NS).get("url")[1:]]
                 for m in root.findall("c:library_materials/c:material", NS)}
    geometries = {g.get("id"): g.find("c:mesh", NS) for g in root.findall("c:library_geometries/c:geometry", NS)}
    parts = []

    def node(element, parent):
        matrix = parent
        for child in element:
            tag = child.tag.split("}")[-1]
            if tag == "matrix":
                local = numbers(child.text)
                # Pinned matrices are rigid rotations/translations, so the
                # normal transform equals their rotation (no nonuniform scale).
                for r in range(3):
                    for c in range(3):
                        dot = sum(local[k * 4 + r] * local[k * 4 + c] for k in range(3))
                        if abs(dot - (1 if r == c else 0)) > 1e-5:
                            raise ValueError("Non-rigid COLLADA matrix")
                matrix = multiply(matrix, local)
            elif tag in ("translate", "rotate", "scale", "lookat", "skew"):
                raise ValueError("Unsupported pinned COLLADA transform: " + tag)
        for instance in element.findall("c:instance_geometry", NS):
            mesh = geometries[instance.get("url")[1:]]
            sources = {}
            for s in mesh.findall("c:source", NS):
                accessor = s.find("c:technique_common/c:accessor", NS)
                values = numbers(s.findtext("c:float_array", namespaces=NS))
                stride = int(accessor.get("stride")); offset = int(accessor.get("offset", "0"))
                sources[s.get("id")] = [values[i:i + 3] for i in range(offset, len(values), stride)]
            vertices = {v.get("id"): v.find("c:input", NS).get("source")[1:]
                        for v in mesh.findall("c:vertices", NS)}
            bindings = {m.get("symbol"): m.get("target")[1:]
                        for m in instance.findall(".//c:instance_material", NS)}
            for primitive in mesh:
                tag = primitive.tag.split("}")[-1]
                if tag in ("source", "vertices"):
                    continue
                if tag != "triangles":
                    raise ValueError("Unsupported COLLADA primitive: " + tag)
                inputs = {i.get("semantic"): (i.get("source")[1:], int(i.get("offset")))
                          for i in primitive.findall("c:input", NS)}
                stride = max(v[1] for v in inputs.values()) + 1
                p_source, p_offset = inputs["VERTEX"]
                n_source, n_offset = inputs["NORMAL"]
                points = sources[vertices[p_source]]; normals = sources[n_source]
                indices = [int(i) for i in primitive.findtext("c:p", namespaces=NS).split()]
                triangles = [(transform(matrix, points[indices[i + p_offset]]),
                              transform(matrix, normals[indices[i + n_offset]], normal=True))
                             for i in range(0, len(indices), stride)]
                parts.append({"color": materials[bindings[primitive.get("material")]], **indexed(triangles)})
        for child in element.findall("c:node", NS):
            node(child, matrix)

    scene_id = root.find("c:scene/c:instance_visual_scene", NS).get("url")[1:]
    visual_scene = next(s for s in root.findall("c:library_visual_scenes/c:visual_scene", NS) if s.get("id") == scene_id)
    for child in visual_scene.findall("c:node", NS):
        node(child, IDENTITY)
    return parts


def build(xacro="xacro"):
    urdf = subprocess.check_output([xacro, str(ROOT / "ros/gazebo/rosbot_xl.urdf.xacro"),
                                   "vendor_dir:=" + str(VENDOR)], text=True)
    robot = ET.fromstring(urdf)
    asset = {"model": "ROSbot XL + OpenMANIPULATOR-X", "root": "base_link",
             "adapter_sha256": hashlib.sha256((ROOT / "ros/gazebo/rosbot_xl.urdf.xacro").read_bytes()).hexdigest(),
             "converter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "sources": json.loads((VENDOR / "manifest.json").read_text()),
             "links": [], "joints": [], "meshes": {}}
    for link in robot.findall("link"):
        row = {"name": link.get("name"), "visuals": []}
        for visual in link.findall("visual"):
            geometry = visual.find("geometry")
            mesh = geometry.find("mesh")
            value = {"origin": origin(visual.find("origin"))}
            color = visual.find("material/color")
            if color is not None:
                value["color"] = numbers(color.get("rgba"))
            if mesh is not None:
                uri = mesh.get("filename"); source = mesh_path(uri)
                if uri not in asset["meshes"]:
                    asset["meshes"][uri] = {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                             "parts": collada(source) if source.suffix == ".dae" else stl(source)}
                value.update(mesh=uri, scale=numbers(mesh.get("scale"), "1 1 1"))
            else:
                shape = list(geometry)[0]
                value.update(shape=shape.tag, dimensions={k: numbers(v) for k, v in shape.attrib.items()})
            row["visuals"].append(value)
        asset["links"].append(row)
    for joint in robot.findall("joint"):
        row = {"name": joint.get("name"), "type": joint.get("type"),
               "parent": joint.find("parent").get("link"), "child": joint.find("child").get("link"),
               "origin": origin(joint.find("origin")), "axis": [0, 0, 1], "initial": 0}
        axis = joint.find("axis")
        if axis is not None:
            row["axis"] = numbers(axis.get("xyz"))
        limit = joint.find("limit")
        if limit is not None:
            row["limit"] = {k: float(v) for k, v in limit.attrib.items()}
        mimic = joint.find("mimic")
        if mimic is not None:
            row["mimic"] = {"joint": mimic.get("joint"), "multiplier": float(mimic.get("multiplier", "1")),
                            "offset": float(mimic.get("offset", "0"))}
        initial = robot.find(f"ros2_control/joint[@name='{row['name']}']/state_interface[@name='position']/param[@name='initial_value']")
        if initial is not None:
            row["initial"] = float(initial.text)
        asset["joints"].append(row)
    data = (json.dumps(asset, separators=(",", ":")) + "\n").encode()
    target = ROOT / "sim/assets/rosbot-xl.json"
    target.write_bytes(data)
    target.with_suffix(".json.gz").write_bytes(gzip.compress(data, mtime=0))
    print(f"Product asset: {len(data):,} bytes; gzip {target.with_suffix('.json.gz').stat().st_size:,} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--xacro", default="xacro")
    build(parser.parse_args().xacro)
