#!/usr/bin/env python3
"""Build the same original cosmetic robot meshes for Gazebo and Three.js.

All dimensions are metres in ROS coordinates (x forward, y left, z up).
This asset never supplies collision geometry, inertia or extra moving joints.
Run from any directory; outputs are deterministic and need no third-party tools.
"""
from pathlib import Path
import json
import math
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PALETTE = {
    'shell': {'color': '#eceef0', 'metalness': 0.0, 'roughness': 0.55},
    'graphite': {'color': '#232c31', 'metalness': 0.3, 'roughness': 0.42},
    'rubber': {'color': '#161d21', 'metalness': 0.0, 'roughness': 0.87},
    'glass': {'color': '#0e202a', 'metalness': 0.5, 'roughness': 0.18},
    'alloy': {'color': '#8c9da5', 'metalness': 0.72, 'roughness': 0.28},
    'signal': {'color': '#70d8d1', 'metalness': 0.2, 'roughness': 0.3},
}
PARTS = []


def rounded_ring(width, depth, radius, z):
    return [(cx + radius * math.cos(a + i * math.pi / 16),
             cy + radius * math.sin(a + i * math.pi / 16), z)
            for cx, cy, a in ((width / 2 - radius, depth / 2 - radius, 0),
                              (-width / 2 + radius, depth / 2 - radius, math.pi / 2),
                              (-width / 2 + radius, -depth / 2 + radius, math.pi),
                              (width / 2 - radius, -depth / 2 + radius, math.pi * 1.5))
            for i in range(9)]


def loft(rings):
    n = len(rings[0]); vertices = [v for ring in rings for v in ring]; indices = []
    for row in range(len(rings) - 1):
        for i in range(n):
            a = row * n + i; b = row * n + (i + 1) % n
            indices.extend((a, b, b + n, a, b + n, a + n))
    for ring_index, flip in ((0, True), (len(rings) - 1, False)):
        ring = rings[ring_index]; center = len(vertices)
        vertices.append(tuple(sum(v[c] for v in ring) / n for c in range(3)))
        for i in range(n):
            a = ring_index * n + i; b = ring_index * n + (i + 1) % n
            indices.extend((center, b, a) if flip else (center, a, b))
    return vertices, indices


def box(size, radius, bevel=0.005):
    x, y, z = size; bevel = min(bevel, z / 3, radius / 2)
    return loft([rounded_ring(x - 2 * bevel, y - 2 * bevel, radius - bevel, -z / 2),
                 rounded_ring(x, y, radius, -z / 2 + bevel),
                 rounded_ring(x, y, radius, z / 2 - bevel),
                 rounded_ring(x - 2 * bevel, y - 2 * bevel, radius - bevel, z / 2)])


def cylinder(radius, height, bevel=0.003):
    bevel = min(bevel, height / 3)
    return loft([[(r * math.cos(i * math.tau / 64), r * math.sin(i * math.tau / 64), z)
                  for i in range(64)] for r, z in
                 ((radius - bevel, -height / 2), (radius, -height / 2 + bevel),
                  (radius, height / 2 - bevel), (radius - bevel, height / 2))])


def ring(outer, inner, height):
    bevel = .001
    section = ((outer - bevel, -height / 2), (outer, -height / 2 + bevel),
               (outer, height / 2 - bevel), (outer - bevel, height / 2),
               (inner + bevel, height / 2), (inner, height / 2 - bevel),
               (inner, -height / 2 + bevel), (inner + bevel, -height / 2))
    rings = [[(r * math.cos(i * math.tau / 64), r * math.sin(i * math.tau / 64), z)
              for i in range(64)] for r, z in section]
    vertices = [v for row in rings for v in row]; indices = []
    for row in range(len(rings)):
        for i in range(64):
            a = row * 64 + i; b = row * 64 + (i + 1) % 64
            c = ((row + 1) % len(rings)) * 64 + (i + 1) % 64
            d = ((row + 1) % len(rings)) * 64 + i
            indices.extend((a, b, c, a, c, d))
    return vertices, indices


def part(name, link, material, geometry, position=(0, 0, 0), rotation=0):
    vertices, indices = geometry; c, s = math.cos(rotation), math.sin(rotation)
    vertices = [(c * x - s * y + position[0], s * x + c * y + position[1], z + position[2])
                for x, y, z in vertices]
    normals = [[0., 0., 0.] for _ in vertices]
    for i in range(0, len(indices), 3):
        a, b, d = [vertices[j] for j in indices[i:i + 3]]
        u = [b[j] - a[j] for j in range(3)]; v = [d[j] - a[j] for j in range(3)]
        normal = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
        for j in indices[i:i + 3]:
            for axis in range(3): normals[j][axis] += normal[axis]
    normals = [[c / (math.sqrt(sum(v * v for v in n)) or 1) for c in n] for n in normals]
    PARTS.append({'name': name, 'link': link, 'material': material,
                  'positions': [round(v, 7) for p in vertices for v in p],
                  'normals': [round(v, 7) for p in normals for v in p], 'indices': indices})


def build():
    part('bumper', 'chassis', 'rubber', box((.414, .284, .07), .075, .01), (0, 0, .145))
    part('caster_mount', 'chassis', 'graphite', box((.055, .052, .042), .014), (-.17, 0, .111))
    part('lower_pan', 'chassis', 'graphite', box((.392, .284, .077), .072, .012), (0, 0, .197))
    part('sculpted_shell', 'chassis', 'shell', loft([
        rounded_ring(.386, .307, .074, .207), rounded_ring(.40, .316, .078, .226),
        rounded_ring(.385, .307, .080, .29), rounded_ring(.354, .282, .072, .309)]))
    part('floating_deck', 'chassis', 'graphite', box((.261, .232, .025), .050), (-.036, 0, .316))
    # A cosmetic front panel; no functional sensor is implied.
    part('front_panel', 'chassis', 'glass', box((.018, .183, .049), .007), (.191, 0, .256))
    part('status_strip', 'chassis', 'signal', box((.01, .106, .006), .002, .001), (.197, 0, .283))
    for side in (-1, 1):
        part('flank_'+str(side), 'chassis', 'graphite', box((.224, .013, .037), .005), (-.013, side * .155, .22))
        part('flank_inlay_'+str(side), 'chassis', 'alloy', box((.114, .014, .004), .002, .0005), (-.025, side * .157, .227))
        for i in range(4):
            part('vent_'+str(side)+'_'+str(i), 'chassis', 'glass', box((.006, .014, .018), .002), (-.1 + i * .016, side * .156, .263))
    part('rear_service_cap', 'chassis', 'graphite', cylinder(.040, .015), (-.105, 0, .337))
    part('shoulder_pedestal', 'chassis', 'alloy', cylinder(.052, .022), (0, 0, .3395))
    part('joint_housing', 'arm', 'graphite', cylinder(.049, .046), (0, 0, .012))
    part('joint_lid', 'arm', 'shell', cylinder(.043, .009), (0, 0, .039))
    part('arm_spar', 'arm', 'alloy', box((.30, .052, .04), .021), (.15, 0, .002))
    part('arm_fairing', 'arm', 'shell', box((.243, .064, .041), .027, .008), (.132, 0, .015))
    part('arm_inlay', 'arm', 'graphite', box((.139, .021, .006), .01, .001), (.148, 0, .039))
    part('tool_housing', 'arm', 'graphite', box((.048, .068, .055), .016), (.285, 0, .003))
    part('fixed_tool_plate', 'arm', 'alloy', box((.013, .057, .039), .005), (.312, 0, .003))
    part('tire', 'wheel', 'rubber', cylinder(.1, .041, .005))
    for side in (-1, 1):
        part('rim_'+str(side), 'wheel', 'alloy', ring(.072, .058, .016), (0, 0, side * .023))
        part('rim_recess_'+str(side), 'wheel', 'graphite', cylinder(.062, .008, .001), (0, 0, side * .025))
        for i in range(5):
            angle = i * math.tau / 5
            part('spoke_'+str(side)+'_'+str(i), 'wheel', 'alloy', box((.044, .013, .006), .005, .001),
                 (.031 * math.cos(angle), .031 * math.sin(angle), side * .029), angle)
        part('hub_'+str(side), 'wheel', 'graphite', cylinder(.022, .011), (0, 0, side * .026))


def collada(link):
    root = ET.Element('COLLADA', xmlns='http://www.collada.org/2005/11/COLLADASchema', version='1.4.1')
    asset = ET.SubElement(root, 'asset'); ET.SubElement(asset, 'unit', name='meter', meter='1'); ET.SubElement(asset, 'up_axis').text = 'Z_UP'
    effects = ET.SubElement(root, 'library_effects'); materials = ET.SubElement(root, 'library_materials')
    for name, spec in PALETTE.items():
        color = [int(spec['color'][i:i+2], 16) / 255 for i in (1, 3, 5)]
        effect = ET.SubElement(effects, 'effect', id=name+'-effect')
        technique = ET.SubElement(ET.SubElement(effect, 'profile_COMMON'), 'technique', sid='common')
        phong = ET.SubElement(technique, 'phong')
        for term, values in (('ambient', color + [1]), ('diffuse', color + [1]), ('specular', [.22, .22, .22, 1])):
            ET.SubElement(ET.SubElement(phong, term), 'color').text = ' '.join(str(round(v, 6)) for v in values)
        ET.SubElement(ET.SubElement(phong, 'shininess'), 'float').text = '45'
        material = ET.SubElement(materials, 'material', id=name)
        ET.SubElement(material, 'instance_effect', url='#'+name+'-effect')
    geometries = ET.SubElement(root, 'library_geometries')
    visual_scene = ET.SubElement(ET.SubElement(root, 'library_visual_scenes'), 'visual_scene', id='Scene')
    for index, part_data in enumerate(p for p in PARTS if p['link'] == link):
        key = 'mesh'+str(index); mesh = ET.SubElement(ET.SubElement(geometries, 'geometry', id=key), 'mesh')
        for name in ('positions', 'normals'):
            values = part_data[name]; source = ET.SubElement(mesh, 'source', id=key+'-'+name)
            ET.SubElement(source, 'float_array', id=key+'-'+name+'-array', count=str(len(values))).text = ' '.join(map(str, values))
            accessor = ET.SubElement(ET.SubElement(source, 'technique_common'), 'accessor', source='#'+key+'-'+name+'-array', count=str(len(values)//3), stride='3')
            for axis in ('X','Y','Z'): ET.SubElement(accessor, 'param', name=axis, type='float')
        vertices = ET.SubElement(mesh, 'vertices', id=key+'-vertices')
        ET.SubElement(vertices, 'input', semantic='POSITION', source='#'+key+'-positions')
        triangles = ET.SubElement(mesh, 'triangles', count=str(len(part_data['indices'])//3), material=part_data['material'])
        ET.SubElement(triangles, 'input', semantic='VERTEX', source='#'+key+'-vertices', offset='0')
        ET.SubElement(triangles, 'input', semantic='NORMAL', source='#'+key+'-normals', offset='1')
        ET.SubElement(triangles, 'p').text = ' '.join(str(v) for i in part_data['indices'] for v in (i,i))
        node = ET.SubElement(visual_scene, 'node', id=key+'-node')
        instance = ET.SubElement(node, 'instance_geometry', url='#'+key)
        common = ET.SubElement(ET.SubElement(instance, 'bind_material'), 'technique_common')
        ET.SubElement(common, 'instance_material', symbol=part_data['material'], target='#'+part_data['material'])
    ET.SubElement(ET.SubElement(root, 'scene'), 'instance_visual_scene', url='#Scene')
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    build()
    asset_dir = ROOT/'sim/assets'; asset_dir.mkdir(exist_ok=True)
    (asset_dir/'haetae-rig.json').write_text(json.dumps({'version':1, 'coordinates':'ROS z-up', 'materials':PALETTE, 'parts':PARTS}, separators=(',',':'))+'\n')
    mesh_dir = ROOT/'ros/gazebo/meshes'; mesh_dir.mkdir(exist_ok=True)
    for link in ('chassis','arm','wheel'): (mesh_dir/(link+'.dae')).write_bytes(collada(link))
    print(f'{len(PARTS)} parts; {sum(len(p["indices"])//3 for p in PARTS)} triangles')
