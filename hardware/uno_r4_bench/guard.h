#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// LED bench only. USB is a trusted transport, not an authenticated boundary.
// Unsigned subtraction keeps short deadlines valid across millis() rollover.
class BenchGuard {
 public:
  static constexpr uint32_t lease_ms = 200;
  static constexpr uint32_t challenge_ms = 100;
  static constexpr uint32_t partial_ms = 100;
  static constexpr size_t frame_size = 96;

  bool on() const { return on_; }
  bool armed() const { return armed_; }
  const char* reason() const { return reason_; }
  const char* session() const { return session_; }
  uint32_t sequence() const { return sequence_; }
  uint32_t challenge() const { return challenge_; }

  void fail(const char* reason) {
    on_ = armed_ = can_arm_ = false;
    reason_ = reason;
    ok_ = false;
  }

  void tick(uint32_t now) {
    if (armed_ && uint32_t(now - renewed_) >= lease_ms) fail("lease");
    if (length_ && uint32_t(now - partial_started_) >= partial_ms) {
      fail("partial");
      length_ = 0;
      discard_ = true;
    }
  }

  // Returns true only when a complete response should be sent. No allocation.
  bool feed(char c, uint32_t now) {
    tick(now);
    if (c == '\n') {
      if (discard_) {
        discard_ = false;
        length_ = 0;
        ok_ = false;
      } else {
        line_[length_] = '\0';
        command(now);
        length_ = 0;
      }
      return true;
    }
    if (discard_) return false;
    if (c < 32 || c > 126 || length_ >= frame_size - 1) {
      fail("protocol");
      length_ = 0;
      discard_ = true;
      return false;
    }
    if (!length_) partial_started_ = now;
    line_[length_++] = c;
    return false;
  }

  size_t reply(char* out, size_t capacity) const {
    int n = snprintf(out, capacity, "H1 %s %s %lu %lu %s %s\n",
                     ok_ ? "OK" : "ERR", session_,
                     static_cast<unsigned long>(sequence_),
                     static_cast<unsigned long>(challenge_),
                     on_ ? "ON" : (armed_ ? "ARMED" : "LOCKED"), reason_);
    return n > 0 && size_t(n) < capacity ? size_t(n) : 0;
  }

 private:
  char session_[17] = "0000000000000000";
  char line_[frame_size] = {};
  size_t length_ = 0;
  uint32_t sequence_ = 0, challenge_ = 0, issued_ = 0, renewed_ = 0;
  uint32_t partial_started_ = 0;
  bool on_ = false, armed_ = false, can_arm_ = false;
  bool discard_ = false, ok_ = false, connected_ = false;
  const char* reason_ = "boot";

  static bool number(const char* s, uint32_t& value) {
    if (!*s) return false;
    value = 0;
    for (; *s; ++s) {
      if (*s < '0' || *s > '9') return false;
      uint32_t digit = uint32_t(*s - '0');
      if (value > (UINT32_MAX - digit) / 10) return false;
      value = value * 10 + digit;
    }
    return true;
  }

  void command(uint32_t now) {
    char* fields[5];
    char* p = line_;
    for (int i = 0; i < 5; ++i) {
      fields[i] = p;
      if (!*p) { fail("protocol"); return; }
      while (*p && *p != ' ') ++p;
      if (i < 4) {
        if (*p != ' ') { fail("protocol"); return; }
        *p++ = '\0';
      } else if (*p) { fail("protocol"); return; }
    }
    uint32_t seq, token;
    if (strcmp(fields[0], "H1") || strlen(fields[2]) != 16 ||
        !number(fields[3], seq) || !number(fields[4], token)) {
      fail("protocol"); return;
    }
    for (const char* s = fields[2]; *s; ++s) {
      if (!((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'f'))) {
        fail("protocol"); return;
      }
    }
    const char* op = fields[1];
    if (!strcmp(op, "HELLO")) {
      // Opening a NEW operator session always removes existing output first.
      fail("hello");
      if (seq || token || (connected_ && !strcmp(fields[2], session_))) {
        fail("replay"); return;
      }
      strcpy(session_, fields[2]);
      connected_ = can_arm_ = ok_ = true;
      sequence_ = 0;
      challenge_ = 1;
      issued_ = now;
      return;
    }
    if (!connected_ || strcmp(fields[2], session_) || sequence_ == UINT32_MAX ||
        seq != sequence_ + 1 || token != challenge_) {
      fail("replay"); return;
    }
    // STOP may always remove output even with an old challenge.
    if (strcmp(op, "STOP") && uint32_t(now - issued_) >= challenge_ms) {
      fail("stale"); return;
    }
    if (challenge_ == UINT32_MAX) { fail("replay"); return; }
    if (!strcmp(op, "ARM")) {
      if (!can_arm_) { fail("unarmed"); return; }
      can_arm_ = false;
      armed_ = true;
      renewed_ = now;
      reason_ = "armed";
    } else if (!strcmp(op, "RUN")) {
      if (!armed_) { fail("unarmed"); return; }
      on_ = true;
      renewed_ = now;
      reason_ = "run";
    } else if (!strcmp(op, "STOP")) {
      fail("stop");
    } else if (strcmp(op, "STATUS")) {
      fail("protocol"); return;
    }
    sequence_ = seq;
    ++challenge_;
    issued_ = now;
    ok_ = true;
  }
};
