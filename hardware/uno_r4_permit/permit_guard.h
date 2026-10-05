#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "src/vendor/monocypher-ed25519.h"

// H2 deliberately permits only the built-in LED. No velocity/joint abstraction.
class PermitGuard {
 public:
  static constexpr uint32_t lease_ms = 200, permit_ms = 50, partial_ms = 100;
  static constexpr uint32_t bind_ms = 500; // OFF-only authentication, never actuation.
  static constexpr size_t frame_size = 256, payload_size = 76, bind_size = 140;
  using Clock = uint32_t (*)();
  PermitGuard(uint32_t epoch, const uint8_t install[16], const uint8_t key[32],
              const uint8_t challenge_key[32], Clock clock)
      : epoch_(epoch), clock_(clock) {
    memcpy(install_, install, 16);
    memcpy(key_, key, 32);
    memcpy(challenge_key_, challenge_key, 32);
    ready_ = epoch != 0;
    bool nonzero = false;
    for (uint8_t byte : key_) nonzero |= byte != 0;
    ready_ &= nonzero;
    static const uint8_t domain[] = "HAETAE-X25519-H2";
    crypto_blake2b_keyed(controller_private_, 32, challenge_key_, 32, domain, sizeof(domain)-1);
    crypto_x25519_public_key(controller_public_, controller_private_);
    nonzero = false;
    for (uint8_t byte : challenge_key_) nonzero |= byte != 0;
    ready_ &= nonzero;
    if (!ready_) fail("provision");
  }
  bool on() const { return on_; }
  bool armed() const { return armed_; }
  const char* reason() const { return reason_; }
  uint32_t sequence() const { return sequence_; }
  uint32_t renewed_at() const { return issued_for_lease_; }
  uint32_t verification_ms() const { return verification_ms_; }
  void fail(const char* reason) {
    on_ = armed_ = can_arm_ = false;
    bound_ = can_bind_ = false;
    crypto_wipe(session_key_, sizeof(session_key_));
    ok_ = false;
    reason_ = reason;
  }
  void tick(uint32_t now) {
    if (armed_ && uint32_t(now - issued_for_lease_) >= duration_) fail("lease");
    if (length_ && uint32_t(now - partial_started_) >= partial_ms) {
      fail("partial"); length_ = 0; discard_ = true;
    }
  }
  bool feed(char c, uint32_t now) {
    tick(now);
    if (c == '\n') {
      if (discard_) { discard_ = false; length_ = 0; ok_ = false; }
      else { line_[length_] = 0; length_ = 0; command(now); }
      return true;
    }
    if (discard_) return false;
    if (c < 32 || c > 126 || length_ >= frame_size - 1) {
      fail("protocol"); length_ = 0; discard_ = true; return false;
    }
    if (!length_) partial_started_ = now;
    line_[length_++] = c;
    return false;
  }
  size_t reply(char* out, size_t capacity) const {
    char id[33], nonce[33]; hex(install_, 16, id); hex(nonce_, 16, nonce);
    int n = snprintf(out, capacity, "H2 %s %s %lu %lu %lu %lu %lu %s %s %s\n",
        ok_ ? "OK" : "ERR", id, (unsigned long)epoch_, (unsigned long)generation_,
        (unsigned long)sequence_, (unsigned long)challenge_, (unsigned long)issued_,
        nonce, on_ ? "ON" : armed_ ? "ARMED" : bound_ ? "BOUND" : "LOCKED", reason_);
    return n > 0 && size_t(n) < capacity ? size_t(n) : 0;
  }
  // Signed key exchange is OFF-only; fast per-action MAC preserves the 50ms gate.
  bool verifying_frame() const { return length_ >= 7 && (!strncmp(line_, "H2 RUN ", 7) || !strncmp(line_, "H2 ARM ", 7) || !strncmp(line_, "H2 BIND ", 8)); }
  static void payload(uint8_t out[payload_size], const uint8_t install[16],
                      uint32_t epoch, uint32_t generation, uint32_t seq,
                      uint32_t challenge, uint32_t issued, uint32_t duration, uint32_t action,
                      const uint8_t nonce[16]) {
    static const char domain[16] = "HAETAE-LED-H2";
    memcpy(out, domain, 16); memcpy(out + 16, install, 16);
    const uint32_t fields[] = {epoch, generation, seq, challenge, issued, duration, action};
    for (size_t i = 0; i < 7; ++i)
      for (size_t j = 0; j < 4; ++j) out[32 + i * 4 + j] = uint8_t(fields[i] >> (j * 8));
    memcpy(out + 60, nonce, 16);
  }
  static void bind_payload(uint8_t out[bind_size], const uint8_t install[16],
                           uint32_t epoch, uint32_t generation, uint32_t seq, uint32_t challenge,
                           uint32_t issued, const uint8_t nonce[16], const uint8_t peer[32],
                           const uint8_t controller[32]) {
    payload(out, install, epoch, generation, seq, challenge, issued, 0, 2, nonce);
    static const char domain[16] = "HAETAE-KEX-H2";
    memcpy(out, domain, 16); memcpy(out + 76, peer, 32); memcpy(out + 108, controller, 32);
  }
 private:
  uint8_t install_[16], key_[32], challenge_key_[32], nonce_[16] = {};
  uint8_t controller_private_[32], controller_public_[32], session_key_[32] = {};
  uint32_t epoch_, generation_ = 0, sequence_ = 0, challenge_ = 0, issued_ = 0;
  uint32_t issued_for_lease_ = 0, duration_ = 0, partial_started_ = 0, verification_ms_ = 0;
  Clock clock_;
  char line_[frame_size] = {};
  size_t length_ = 0;
  bool ready_ = false, on_ = false, armed_ = false, can_arm_ = false, ok_ = false, discard_ = false;
  bool bound_ = false, can_bind_ = false;
  const char* reason_ = "boot";
  void issue(uint32_t now) {
    issued_ = now;
    uint8_t bytes[payload_size], zero[16] = {};
    payload(bytes, install_, epoch_, generation_, sequence_, challenge_, issued_, 0, 2, zero);
    crypto_blake2b_keyed(nonce_, sizeof(nonce_), challenge_key_, sizeof(challenge_key_), bytes, sizeof(bytes));
  }
  static void hex(const uint8_t* data, size_t n, char* out) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < n; ++i) { out[2*i] = digits[data[i] >> 4]; out[2*i+1] = digits[data[i] & 15]; }
    out[2*n] = 0;
  }
  static bool number(const char* s, uint32_t& value) {
    if (!*s || (*s == '0' && s[1])) return false;
    value = 0;
    for (; *s; ++s) {
      if (*s < '0' || *s > '9' || value > (UINT32_MAX - uint32_t(*s - '0')) / 10) return false;
      value = value * 10 + uint32_t(*s - '0');
    }
    return true;
  }
  static bool unhex(const char* s, uint8_t* out, size_t size) {
    if (strlen(s) != size*2) return false;
    for (size_t i = 0; i < size*2; ++i) {
      unsigned value;
      if (s[i] >= '0' && s[i] <= '9') value = unsigned(s[i] - '0');
      else if (s[i] >= 'a' && s[i] <= 'f') value = unsigned(s[i] - 'a') + 10;
      else return false;
      if (!(i & 1)) out[i/2] = uint8_t(value << 4); else out[i/2] |= uint8_t(value);
    }
    return true;
  }
  void command(uint32_t now) {
    if (!strcmp(line_, "H2 STOP")) { fail("stop"); ok_ = true; return; }
    if (!ready_) { fail("provision"); return; }
    if (!strcmp(line_, "H2 HELLO")) {
      fail("hello");
      if (generation_ == UINT32_MAX) { ready_ = false; fail("exhausted"); return; }
      ++generation_; sequence_ = 0; challenge_ = 1; issue(now);
      can_bind_ = ok_ = true; return;
    }
    if (!strcmp(line_, "H2 STATUS")) {
      if (!generation_ || challenge_ == UINT32_MAX) { fail("exhausted"); return; }
      ++challenge_; issue(now); ok_ = true; return;
    }
    bool binding = !strncmp(line_, "H2 BIND ", 8);
    size_t count = binding ? 9 : 8;
    char* fields[9]; char* p = line_;
    for (size_t i = 0; i < count; ++i) {
      fields[i] = p; if (!*p) { fail("protocol"); return; }
      while (*p && *p != ' ') ++p;
      if (i < count-1) { if (*p != ' ') { fail("protocol"); return; } *p++ = 0; }
      else if (*p) { fail("protocol"); return; }
    }
    uint32_t generation, seq, token, stamp, duration;
    uint8_t sig[64], bytes[bind_size], peer[32];
    bool arm = !strcmp(fields[1], "ARM"), run = !strcmp(fields[1], "RUN");
    if (strcmp(fields[0], "H2") || (!arm && !run && !binding) || !number(fields[2], generation) ||
        !number(fields[3], seq) || !number(fields[4], token) || !number(fields[5], stamp) ||
        !number(fields[6], duration) || !unhex(fields[binding ? 8 : 7], sig, binding ? 64 : 32) ||
        (binding && !unhex(fields[7], peer, 32))) { fail("protocol"); return; }
    if (!generation_ || generation != generation_ || sequence_ == UINT32_MAX ||
        seq != sequence_ + 1 || token != challenge_ || challenge_ == UINT32_MAX || stamp != issued_) {
      fail("replay"); return;
    }
    if (binding) {
      if (duration || !can_bind_ || bound_ || armed_ || on_) { fail("unarmed"); return; }
      if (uint32_t(now - issued_) >= bind_ms) { fail("stale"); return; }
      bind_payload(bytes, install_, epoch_, generation, seq, token, stamp, nonce_, peer, controller_public_);
    } else {
      if (!duration || duration > lease_ms || uint32_t(now - issued_) >= permit_ms) { fail("stale"); return; }
      if (!bound_ || (arm && !can_arm_) || (run && !armed_)) { fail("unarmed"); return; }
      payload(bytes, install_, epoch_, generation, seq, token, stamp, duration, run ? 1 : 0, nonce_);
    }
    on_ = false; // Physical sketch removes output before calling this verifier.
    uint32_t before = clock_();
    int result;
    if (binding) {
      result = crypto_ed25519_check(sig, key_, bytes, bind_size);
      if (!result) {
        uint8_t shared[32], zero[32] = {};
        crypto_x25519(shared, controller_private_, peer);
        result = !crypto_verify32(shared, zero);
        if (!result) crypto_blake2b_keyed(session_key_, 32, shared, 32, bytes, bind_size);
        crypto_wipe(shared, sizeof(shared));
      }
    } else {
      uint8_t mac[32];
      crypto_blake2b_keyed(mac, sizeof(mac), session_key_, sizeof(session_key_), bytes, payload_size);
      result = crypto_verify32(sig, mac);
    }
    uint32_t after = clock_(); verification_ms_ = uint32_t(after - before);
    tick(after);  // Verification cannot extend a lease or conceal its expiry.
    if (result) { fail("signature"); return; }
    if (binding) {
      if (!can_bind_ || uint32_t(after - issued_) >= bind_ms) { fail("stale"); return; }
      can_bind_ = false; bound_ = can_arm_ = true;
      sequence_ = seq; ++challenge_; issue(after);
      reason_ = "bound"; ok_ = true; return;
    }
    if (verification_ms_ >= permit_ms || uint32_t(after - issued_) >= permit_ms ||
        uint32_t(after - issued_) >= duration) { fail("stale"); return; }
    if ((arm && !can_arm_) || (run && !armed_)) { fail("lease"); return; }
    can_arm_ = false; armed_ = true; on_ = run;
    issued_for_lease_ = issued_; duration_ = duration;
    sequence_ = seq; ++challenge_; issue(after);
    reason_ = run ? "run" : "armed"; ok_ = true;
  }
};
