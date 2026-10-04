#!/bin/sh
# Build the browser simulator: compile haetae-core to WebAssembly, generate
# the JS bindings, and copy the dinner-party example next to the page.
#
#   sim/build.sh              build into sim/
#   sim/build.sh --stage DIR  also copy the built site to DIR (e.g. to serve it)
#
# Needs: rustup target add wasm32-unknown-unknown
#        cargo install wasm-bindgen-cli --version <same as Cargo.lock>
set -eu
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--stage" ]; then
  stage_path=$(python3 tools/safe_stage.py "${2:?stage directory required}")
fi

cargo build -p haetae-wasm --release --target wasm32-unknown-unknown
wasm-bindgen --target web --no-typescript --out-dir sim/pkg \
  target/wasm32-unknown-unknown/release/haetae_wasm.wasm
mkdir -p sim/examples
cp examples/dinner-party/policy.json examples/dinner-party/world.json \
  examples/dinner-party/proposals.jsonl sim/examples/

if [ "${1:-}" = "--stage" ]; then
  rsync -a --delete --exclude build.sh --exclude .haetae-sim-stage sim/ "$stage_path/"
  echo "staged to $stage_path"
fi
