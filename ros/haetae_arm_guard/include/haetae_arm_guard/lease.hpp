#pragma once
#include <cstdint>

namespace haetae_arm_guard
{
struct Lease
{
  uint64_t sent_ms = 0;
  int64_t received_ns = 0;
};

inline bool admissible(uint64_t stamp, uint64_t last, int64_t now_ms)
{
  if (!stamp || stamp <= last || now_ms < 0) {return false;}
  const auto now = static_cast<uint64_t>(now_ms);
  return stamp > now ? stamp - now <= 20 : now - stamp <= 50;
}

inline bool fresh(const Lease & lease, int64_t sim_ms, int64_t wall_ns)
{
  return lease.sent_ms > 0 && sim_ms >= 0 && wall_ns >= lease.received_ns &&
         static_cast<uint64_t>(sim_ms) >= lease.sent_ms &&
         static_cast<uint64_t>(sim_ms) - lease.sent_ms < 250 &&
         wall_ns - lease.received_ns < 250000000;
}
}  // namespace haetae_arm_guard
