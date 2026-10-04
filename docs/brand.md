# Haetae brand assets

The user approved **Haetae A (seated guardian)** and **haetae-robotics B (kinematic link)** on 2026-10-04. Product identity uses the guardian; the organization uses the articulated link. Exact lowercase wordmarks are `haetae` and `haetae-robotics`.

[Open the identity preview](../sim/brand/index.html).

## Files

Both folders have the same variants:

| File | Use |
| --- | --- |
| `logo.svg` / `logo.png` | Horizontal color identity on a light background |
| `logo-light.svg` / `logo-light.png` | Horizontal identity on graphite or another dark background |
| `logo-mono.svg` / `logo-mono.png` | One-color horizontal identity; eye or pin hole is transparent |
| `mark.svg` / `mark.png` | Symbol only on a light background |
| `mark-light.svg` / `mark-light.png` | Symbol only on a dark background |
| `mark-mono.svg` / `mark-mono.png` | One-color symbol, with transparent negative space |
| `mark-adaptive.svg#mark` | Theme-aware monochrome SVG symbol for `<use>`; inherits `currentColor` |
| `favicon.svg` / `favicon.ico` | Graphite tile with the optical symbol; ICO contains 16 and 32 px |
| `icon-180.png` / `icon-512.png` | App icon exports |

Product files are in [`sim/brand/haetae/`](../sim/brand/haetae/); organization files are in [`sim/brand/haetae-robotics/`](../sim/brand/haetae-robotics/). SVGs contain vector paths, not embedded bitmaps or font-dependent text. PNG logos and marks retain a transparent background. App icons have a graphite tile with transparent rounded corners.

## Color and spacing

- Graphite: `#111418`.
- Light: `#e7ebee`.
- Cyan accent: `#66d5d0`.
- Clear space: at least one quarter of the symbol height outside the artwork.
- Horizontal lockup: preserve the supplied proportions and centered alignment. Use the mark or favicon at small sizes; keep a product wordmark at least 100 px wide and an organization wordmark at least 180 px wide.
- Keep status colors in the existing verdict components. The brand mark is not a verdict, certification badge or safety guarantee.
- Do not stretch, rotate, outline, add gradients/glow, or replace the outlined wordmark with system text.

## Source and rebuilding

[`tools/brand_paths.json`](../tools/brand_paths.json) stores the contours of the approved guardian and wordmarks. [`tools/build_brand_assets.py`](../tools/build_brand_assets.py) normalizes the artwork, constructs the equal-width kinematic links and open joint, and generates all vector variants. The optical icon enlarges the guardian eye and joint hole for small sizes.

```sh
python3 tools/build_brand_assets.py
uv run --with resvg-py --with pillow python tools/build_brand_assets.py --raster
```

The normal build needs only Python's standard library. Raster export tools are build-time dependencies; the simulator needs no new package, remote asset or font.

## Provenance

The preceding six-concept exploration was generated with built-in imagegen after an actual local Claude concept consultation. The approved product A silhouette and A/B wordmarks were separated from their raster concepts and recreated as curves. The organization B links and joint were rebuilt with simple geometry. Final colors, spacing, backgrounds and optical icons are authored vector variants.

Local exploration: `artifacts/brand-logo-20261004/`. Claude's text consultation and invocation evidence: `.omx/artifacts/ask-claude-brand-logos-20261004.md` (prompt SHA256 `8bc4edb319ab8a0cac0f9471b90d391d3ee60efa31e489cbbb61c4e235fcfad1`). This was a concept consultation, not a Claude visual approval of the final SVGs. Final vectors were checked by rendering them locally.

Application: live Gazebo header and favicon; scripted demo header, favicon and pipeline; repository README. Historical v3.9 robot badge geometry remains part of the legacy illustrative model and is not an identity source. The vector identities have not undergone trademark review.
