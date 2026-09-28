# Vendored three.js

three.js 0.186.1 (MIT, see LICENSE), fetched from https://cdn.jsdelivr.net/npm/three@0.186.1/ on 2026-09-27
so the simulator works offline without a CDN.

| File | Source path | SHA-256 |
|---|---|---|
| three.module.js | build/three.module.min.js (jsDelivr-minified build/three.module.js) | 3bc833fceb6577bd1a380388f832ae61cd6e2f78ad02b0bf0d5adf5a4a9334fe |
| three.core.js | build/three.core.min.js (jsDelivr-minified build/three.core.js) | 3b346151f65ffdfca3e4c002bd58966b78c423087fb48a873f83200de1bffc48 |
| OrbitControls.js | examples/jsm/controls/OrbitControls.js | 3d79d07ecb686b4e5d93232eedab255331c1beef711e13164eaa1f68655a5f2b |
| CSS2DRenderer.js | examples/jsm/renderers/CSS2DRenderer.js | 256abfc6ae1aa20eb83c10d7a0e26534527df65872e4d52da6354bee78288c34 |

The core file is saved as `three.core.js` because `three.module.js` imports it by that name.
Pages map the bare specifier `three` to `./vendor/three/three.module.js` with an import map.
