// Thin wrapper over the WebAssembly build of haetae-core.
//
// Every verdict, fired list, clamped action and speed cap in the simulator
// comes from Gate.judge() below. Nothing in JS re-implements a safety check.

let wasm = null;

/** Load the WASM module. Imported dynamically so a missing build can be reported in the page. */
export async function loadEngine() {
  const mod = await import('../pkg/haetae_wasm.js');
  await mod.default(); // fetches ./pkg/haetae_wasm_bg.wasm next to the JS glue
  wasm = mod;
  return { version: mod.version() };
}

/** The policy error message, or null when the policy loads. */
export function policyError(policyText) {
  return wasm.policy_error(policyText) ?? null;
}

/** One gate instance (policy + mode ladder). */
export class Gate {
  /** Throws Error(message) when the policy is invalid (fail-closed). */
  constructor(policyText) {
    this.sim = new wasm.Sim(policyText);
  }

  /**
   * Judge a proposal against a world at the trusted clock `nowMs`.
   * Returns the parsed Decision; throws on malformed input.
   */
  judge(proposal, world, nowMs) {
    const json = this.sim.judge(JSON.stringify(proposal), JSON.stringify(world), Math.floor(nowMs));
    return JSON.parse(json);
  }

  /** Raise the mode; returns whether it changed (lower targets are ignored by the gate). */
  raise(mode) {
    return this.sim.raise_mode(mode);
  }

  reset() {
    this.sim.reset_mode();
  }

  get mode() {
    return this.sim.mode();
  }

  free() {
    this.sim.free();
  }
}
