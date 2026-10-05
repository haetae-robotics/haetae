#pragma once
// Same-host simulation reference. Host/root/controller and trusted clock are
// outside the threat boundary. This mutex/EVP implementation is not hard-RT.
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <limits>
#include <mutex>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace haetae_arm_guard
{
inline int64_t steady_ns()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline std::string hex(const unsigned char * bytes, size_t size)
{
  const char * digits = "0123456789abcdef";
  std::string out;
  for (size_t i = 0; i < size; ++i) {out += digits[bytes[i] >> 4]; out += digits[bytes[i] & 15];}
  return out;
}
inline std::vector<unsigned char> unhex(const std::string & value, size_t size)
{
  if (value.size() != size * 2) {throw std::invalid_argument("hex length");}
  std::vector<unsigned char> out;
  const std::string digits = "0123456789abcdef";
  for (size_t i = 0; i < value.size(); i += 2) {
    auto a = digits.find(value[i]), b = digits.find(value[i + 1]);
    if (a == std::string::npos || b == std::string::npos) {throw std::invalid_argument("hex");}
    out.push_back(static_cast<unsigned char>((a << 4) | b));
  }
  return out;
}
inline uint64_t integer(const std::string & value)
{
  if (value.empty() || (value.size() > 1 && value[0] == '0') || value.size() > 19) {
    throw std::invalid_argument("noncanonical integer");
  }
  uint64_t out = 0;
  for (char c : value) {
    if (c < '0' || c > '9') {throw std::invalid_argument("integer");}
    out = out * 10 + static_cast<unsigned>(c - '0');
  }
  if (out > static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) {
    throw std::invalid_argument("integer overflow");
  }
  return out;
}
inline void append_u64(std::string & out, uint64_t value, size_t count = 8)
{
  for (size_t i = count; i > 0; --i) {out += static_cast<char>(value >> ((i - 1) * 8));}
}
inline void append_double(std::string & out, double value)
{
  static_assert(sizeof(value) == sizeof(uint64_t), "IEEE binary64 required");
  uint64_t bits;
  std::memcpy(&bits, &value, sizeof(bits)); append_u64(out, bits);
}
inline std::string sha256(const std::string & value)
{
  std::array<unsigned char, 32> hash{};
  unsigned int size = 0;
  if (!EVP_Digest(value.data(), value.size(), hash.data(), &size, EVP_sha256(), nullptr) || size != 32) {
    throw std::runtime_error("SHA256 failed");
  }
  return hex(hash.data(), hash.size());
}
inline const std::string idle_digest(64, '0');

struct Grant
{
  uint64_t seq = 0, sim = 0, wall = 0, sim_end = 0, wall_end = 0;
  std::string digest;
};

inline bool current_goal_stamp(int64_t trajectory_stamp, const Grant & grant,
  int64_t cutoff, int64_t now)
{
  // A zero ROS header means start at JTC admission, not an unbounded permit.
  const auto stamp = trajectory_stamp == 0 ? static_cast<int64_t>(grant.sim) : trajectory_stamp;
  if (stamp <= 0 || stamp < cutoff || now < 0) {return false;}
  return stamp > now ? stamp - now <= 20000000 : now - stamp < 50000000;
}

class PermitGuard
{
  std::mutex mutex_;
  std::string public_, target_, nonce_;
  uint64_t seq_ = 0;
  Grant grant_;
  bool locked_ = true;
  uint64_t generation_ = 0;
  uint64_t accepted_ = 0, rejected_ = 0, rejected_motion_ = 0;
  std::string reason_ = "startup";
  bool live_unlocked(int64_t sim, int64_t wall)
  {
    if (!locked_ && (sim < 0 || wall < 0 || static_cast<uint64_t>(sim) < grant_.sim ||
      static_cast<uint64_t>(wall) < grant_.wall || static_cast<uint64_t>(sim) >= grant_.sim_end ||
      static_cast<uint64_t>(wall) >= grant_.wall_end)) {locked_ = true; ++generation_;}
    return !locked_;
  }
public:
  void configure(const std::string & key, const std::string & target)
  {
    unhex(key, 32); public_ = key; target_ = target;
  }
  void activate()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    std::array<unsigned char, 16> random{};
    if (RAND_bytes(random.data(), random.size()) != 1) {throw std::runtime_error("controller RNG failed");}
    nonce_ = hex(random.data(), random.size()); seq_ = 0; grant_ = {}; locked_ = true; ++generation_;
  }
  bool accept(const std::string & token, const std::string & kind, const std::string & digest,
    int64_t sim, int64_t wall, bool reset = false, bool stop = false)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    live_unlocked(sim, wall);
    try {
      if (token.size() > 512 || sim < 0 || wall < 0) {throw std::invalid_argument("token bounds");}
      std::vector<std::string> fields;
      std::istringstream stream(token); std::string field;
      while (std::getline(stream, field, ':')) {fields.push_back(field);}
      if (fields.size() != 10 || fields[0] != "v1" || fields[1] != target_ + "-" + kind ||
        fields[2] != nonce_ || fields[8] != digest) {throw std::invalid_argument("binding");}
      unhex(digest, 32);
      Grant next{integer(fields[3]), integer(fields[4]), integer(fields[5]),
        integer(fields[6]), integer(fields[7]), digest};
      const auto s = static_cast<uint64_t>(sim), w = static_cast<uint64_t>(wall);
      if (!next.seq || next.seq <= seq_ || next.sim > s || next.wall > w ||
        s - next.sim >= 50000000 || w - next.wall >= 50000000 ||
        next.sim_end <= s || next.wall_end <= w || next.sim_end <= next.sim ||
        next.wall_end <= next.wall || next.sim_end - next.sim > 200000000 ||
        next.wall_end - next.wall > 200000000) {throw std::invalid_argument("freshness");}
      auto key_bytes = unhex(public_, 32), signature = unhex(fields[9], 64);
      auto key = EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr, key_bytes.data(), key_bytes.size());
      auto context = EVP_MD_CTX_new();
      const std::string message = std::string("haetae-controller-v1\0", 21) + token.substr(0, token.rfind(':'));
      const bool valid = key && context && EVP_DigestVerifyInit(context, nullptr, nullptr, nullptr, key) == 1 &&
        EVP_DigestVerify(context, signature.data(), signature.size(),
          reinterpret_cast<const unsigned char *>(message.data()), message.size()) == 1;
      EVP_MD_CTX_free(context); EVP_PKEY_free(key);
      if (!valid) {throw std::invalid_argument("signature");}
      if (locked_ && !reset && !stop) {throw std::invalid_argument("locked");}
      seq_ = next.seq;
      ++accepted_;
      if (stop) {locked_ = true; ++generation_; grant_ = {};}
      else {if (reset) {++generation_;} locked_ = false; grant_ = next;}
      return true;
    } catch (const std::exception & e) {
      locked_ = true; ++generation_; ++rejected_;
      if (!stop) {++rejected_motion_;}
      reason_ = e.what(); return false;
    }
  }
  bool fresh(int64_t sim, int64_t wall)
  {
    std::lock_guard<std::mutex> lock(mutex_); return live_unlocked(sim, wall);
  }
  void stop()
  {
    std::lock_guard<std::mutex> lock(mutex_); locked_ = true; ++generation_;
  }
  void reject()
  {
    std::lock_guard<std::mutex> lock(mutex_); locked_ = true; ++generation_; ++rejected_; ++rejected_motion_;
  }
  std::string nonce() {std::lock_guard<std::mutex> lock(mutex_); return nonce_;}
  uint64_t generation() {std::lock_guard<std::mutex> lock(mutex_); return generation_;}
  Grant grant() {std::lock_guard<std::mutex> lock(mutex_); return grant_;}
  uint64_t accepted() {std::lock_guard<std::mutex> lock(mutex_); return accepted_;}
  uint64_t rejected() {std::lock_guard<std::mutex> lock(mutex_); return rejected_;}
  uint64_t rejected_motion() {std::lock_guard<std::mutex> lock(mutex_); return rejected_motion_;}
  std::string reason() {std::lock_guard<std::mutex> lock(mutex_); return reason_;}
};
}  // namespace haetae_arm_guard
