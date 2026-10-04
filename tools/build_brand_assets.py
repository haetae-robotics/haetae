#!/usr/bin/env python3
"""Build the approved Haetae A / haetae-robotics B vector identity.

SVG generation uses only the standard library and outlined, local artwork.
Optional PNG/ICO exports: uv run --with resvg-py --with pillow
    python tools/build_brand_assets.py --raster
"""

import argparse
import io
import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sim" / "brand"
SHAPES = json.loads((ROOT / "tools" / "brand_paths.json").read_text())["shapes"]
GRAPHITE = "#111418"
LIGHT = "#e7ebee"
CYAN = "#66d5d0"


def contours(name):
    return "".join(
        f'<path d="{p["d"]}" transform="{p["transform"]}"/>'
        for p in SHAPES[name]["paths"]
    )


def guardian(ink, accent, mono=False, small=False):
    width, height = SHAPES["guardian"]["size"]
    scale = 128 / height
    left = (128 - width * scale) / 2
    eye = ('<rect x="93" y="23" width="19" height="9" rx="4.5"/>'
           if small else '<rect x="93" y="25" width="17" height="5" rx="2.5"/>')
    body = (f'<g fill="{ink}" transform="translate({left:.4f} 0) '
            f'scale({scale:.6f})">{contours("guardian")}</g>')
    if mono:
        return (f'<defs><mask id="guardian-eye" maskUnits="userSpaceOnUse" '
                f'x="0" y="0" width="128" height="128">'
                f'<rect width="128" height="128" fill="#fff"/>'
                f'<g fill="#000">{eye}</g></mask></defs>'
                f'<g mask="url(#guardian-eye)">{body}</g>')
    return body + f'<g fill="{accent}">{eye}</g>'


def kinematic(ink, accent, mono=False, small=False):
    # Equal-width capsule links; the joint remains open on every background.
    hole = 12 if small else 11
    body = (f'<g stroke="{ink}" stroke-width="34" stroke-linecap="round">'
            '<path d="M37 57V111"/><path d="M37 57 97 17"/></g>'
            f'<circle cx="37" cy="57" r="21" fill="{ink}"/>')
    ring = ("" if mono else
            f'<circle cx="37" cy="57" r="17" fill="{accent}"/>')
    return (f'<defs><mask id="joint-hole" maskUnits="userSpaceOnUse" '
            f'x="0" y="0" width="128" height="128">'
            f'<rect width="128" height="128" fill="#fff"/>'
            f'<circle cx="37" cy="57" r="{hole}" fill="#000"/>'
            f'</mask></defs><g mask="url(#joint-hole)">{body}{ring}</g>')


def mark(name, ink=GRAPHITE, accent=CYAN, mono=False, small=False):
    draw = guardian if name == "haetae" else kinematic
    return draw(ink, accent, mono, small)


def svg(name, body, width=128, height=128, label=None):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width:.4f} {height:.4f}" '
            f'width="{width:.4f}" height="{height:.4f}" role="img" '
            f'aria-labelledby="brand-title"><title id="brand-title">'
            f'{escape(label or name)}</title>{body}</svg>\n')


def logo(name, ink, accent, mono=False):
    word = "haetae-word" if name == "haetae" else "robotics-word"
    width, height = SHAPES[word]["size"]
    scale = 64 / height
    body = (f'<g transform="translate(12 12)">{mark(name, ink, accent, mono)}</g>'
            f'<g fill="{ink}" transform="translate(172 44) '
            f'scale({scale:.6f})">{contours(word)}</g>')
    return svg(name, body, 184 + width * scale, 152)


def icon(name):
    body = (f'<rect width="128" height="128" rx="26" fill="{GRAPHITE}"/>'
            '<g transform="translate(18 16) scale(.72)">'
            f'{mark(name, LIGHT, CYAN, small=True)}</g>')
    return svg(name, body, label=name + " icon")


def adaptive_mark(name):
    # An external sprite inherits the caller's currentColor in the demo.
    body = mark(name, "currentColor", "currentColor", mono=True, small=True)
    return (f'<svg xmlns="http://www.w3.org/2000/svg"><symbol id="mark" '
            f'viewBox="0 0 128 128">{body}</symbol></svg>\n')


def build(raster=False):
    for name in ("haetae", "haetae-robotics"):
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        variants = {
            "mark.svg": svg(name, mark(name)),
            "mark-light.svg": svg(name, mark(name, LIGHT)),
            "mark-mono.svg": svg(name, mark(name, GRAPHITE, GRAPHITE, mono=True)),
            "mark-adaptive.svg": adaptive_mark(name),
            "logo.svg": logo(name, GRAPHITE, CYAN),
            "logo-light.svg": logo(name, LIGHT, CYAN),
            "logo-mono.svg": logo(name, GRAPHITE, GRAPHITE, mono=True),
            "favicon.svg": icon(name),
        }
        for filename, value in variants.items():
            (folder / filename).write_text(value)
        if raster:
            import resvg_py
            from PIL import Image

            for size in (180, 512):
                data = resvg_py.svg_to_bytes(svg_string=icon(name), width=size, height=size)
                (folder / f"icon-{size}.png").write_bytes(data)
            for filename in ("mark.svg", "mark-light.svg", "mark-mono.svg",
                             "logo.svg", "logo-light.svg", "logo-mono.svg"):
                data = resvg_py.svg_to_bytes(svg_string=variants[filename],
                                             width=2048 if filename.startswith("logo") else 1024)
                (folder / filename.replace(".svg", ".png")).write_bytes(data)
            # Render at high resolution before reducing for an ICO container.
            data = resvg_py.svg_to_bytes(svg_string=icon(name), width=256, height=256)
            Image.open(io.BytesIO(data)).save(folder / "favicon.ico", sizes=[(16, 16), (32, 32)])
        print(f"{name}: vector assets in {folder.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", action="store_true", help="also export PNG/ICO with resvg-py and Pillow")
    build(parser.parse_args().raster)
