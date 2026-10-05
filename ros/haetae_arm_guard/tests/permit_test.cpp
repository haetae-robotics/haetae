#include "haetae_arm_guard/permit.hpp"
#include <cstdlib>
#include <iostream>

using namespace haetae_arm_guard;
void check(bool value, const char * name)
{
  if (!value) {std::cerr << name << '\n'; std::exit(1);}
}
// Public fixture only. Deployment uses unpredictable, per-run private keys.
std::string token(const std::string & nonce, uint64_t seq, const std::string & kind,
  uint64_t sim = 1000000000, uint64_t wall = 2000000000,
  uint64_t lease = 200000000, const std::string & digest = idle_digest)
{
  std::string body = "v1:base-" + kind + ":" + nonce + ":" + std::to_string(seq) + ":" +
    std::to_string(sim) + ":" + std::to_string(wall) + ":" + std::to_string(sim + lease) + ":" +
    std::to_string(wall + lease) + ":" + digest;
  const std::array<unsigned char, 32> seed{};
  auto key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, seed.data(), seed.size());
  auto context = EVP_MD_CTX_new();
  const std::string message = std::string("haetae-controller-v1\0", 21) + body;
  std::array<unsigned char, 64> signature{}; size_t size = signature.size();
  check(EVP_DigestSignInit(context, nullptr, nullptr, nullptr, key) == 1 &&
    EVP_DigestSign(context, signature.data(), &size,
      reinterpret_cast<const unsigned char *>(message.data()), message.size()) == 1, "fixture sign");
  EVP_MD_CTX_free(context); EVP_PKEY_free(key);
  return body + ":" + hex(signature.data(), size);
}
int main()
{
  const std::array<unsigned char, 32> seed{};
  auto key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, seed.data(), seed.size());
  std::array<unsigned char, 32> public_key{}; size_t size = public_key.size();
  check(EVP_PKEY_get_raw_public_key(key, public_key.data(), &size) == 1, "fixture public key");
  EVP_PKEY_free(key);
  PermitGuard guard; guard.configure(hex(public_key.data(), size), "base"); guard.activate();
  const auto nonce = guard.nonce();
  auto admit = [&](const std::string & permit, const std::string & kind, bool reset = false,
      uint64_t sim = 1000000000, uint64_t wall = 2000000000) {
    return guard.accept(permit, kind, idle_digest, sim, wall, reset);
  };
  check(!admit(token(nonce, 1, "command"), "command"), "startup must not actuate");
  check(admit(token(nonce, 2, "reset"), "reset", true), "explicit reset");
  auto command = token(nonce, 3, "command");
  check(admit(command, "command"), "fresh exact command");
  check(guard.authorizes(1000000000, 2000000000, idle_digest), "atomic exact grant snapshot");
  check(!guard.authorizes(1000000000, 2000000000, std::string(64, '1')), "wrong digest never authorized");
  check(!admit(command, "command"), "replay rejected and locks");
  check(!guard.authorizes(1000000000, 2000000000, idle_digest), "rejected grant cannot authorize next sample");
  check(guard.grant().seq == 0, "rejection clears stale grant across separate reads");
  check(!admit(token(nonce, 4, "command"), "command"), "fresh command cannot rearm a latch");
  check(admit(token(nonce, 5, "reset"), "reset", true), "fresh reset after rejection");
  auto altered = token(nonce, 6, "command"); altered.back() = altered.back() == '0' ? '1' : '0';
  check(!admit(altered, "command"), "forged signature");
  check(!admit(token(nonce, 7, "reset", 1000000000, 2000000000, 200000001), "reset", true), "overlong lease");
  check(!admit(token(nonce, 8, "reset"), "reset", true, 1050000000), "50ms admission deadline");
  check(!admit(token(nonce, 9, "reset"), "reset", true, 1000000000, 2050000000), "50ms wall admission deadline");
  check(!admit(token(nonce, 10, "reset", 1000000001), "reset", true), "future sim timestamp");
  check(!admit(token(nonce, 11, "reset", 1000000000, 2000000001), "reset", true), "future wall timestamp");
  // Issuer backdating never relaxes verifier checks or extends expiry.
  PermitGuard skew; skew.configure(hex(public_key.data(), size), "base"); skew.activate();
  const uint64_t origin = 1000000000, backdate = 10000000;
  uint64_t sequence = 0;
  for (uint64_t lag : {uint64_t{0}, uint64_t{1000000}, backdate}) {
    check(skew.accept(token(skew.nonce(), ++sequence, "reset", origin - backdate),
      "reset", idle_digest, origin - lag, 2000000000, true), "one-cycle skew conservatively admitted");
    check(skew.grant().sim_end == origin + 200000000 - backdate, "simulation expiry earlier");
    check(skew.grant().wall_end == 2200000000, "wall expiry never extended");
  }
  const auto skew_rejections = skew.rejected();
  check(!skew.accept(token(skew.nonce(), ++sequence, "reset", origin - backdate),
    "reset", idle_digest, origin - backdate - 1, 2000000000, true), "larger skew still future and rejected");
  check(skew.rejected() == skew_rejections + 1 && !skew.fresh(origin, 2000000000),
    "larger skew rejects and locks");
  check(skew.accept(token(skew.nonce(), ++sequence, "reset", origin - backdate),
    "reset", idle_digest, origin, 2000000000, true), "explicit reset after skew rejection");
  check(!skew.fresh(origin + 200000000 - backdate, 2000000000), "backdated simulation expiry enforced");
  check(admit(token(nonce, 12, "reset"), "reset", true), "reset for wall expiry");
  check(!guard.fresh(1000000000, 2200000000), "paused simulation still expires");
  check(guard.grant().seq == 0, "expiry clears stale grant");
  check(!admit(token(nonce, 13, "command", 1000000000, 2200000000), "command", false, 1000000000, 2200000000),
    "recovery cannot revive movement");
  check(admit(token(nonce, 14, "reset"), "reset", true), "reset for sim expiry");
  check(!guard.fresh(1200000000, 2000000000), "sim expiry");
  check(admit(token(nonce, 15, "reset"), "reset", true), "reset for clock rollback");
  check(!guard.fresh(999999999, 2000000000), "clock rollback locks");
  guard.activate();
  check(guard.nonce() != nonce, "activation nonce rotates");
  check(!admit(token(nonce, 16, "reset"), "reset", true), "old activation rejected");
  check(!admit(token(guard.nonce(), 17, "reset", 1000000000, 2000000000, 200000000, std::string(64, '1')),
    "reset", true), "exact payload binding");
  PermitGuard arm; arm.configure(hex(public_key.data(), size), "arm"); arm.activate();
  check(!arm.accept(token(arm.nonce(), 1, "reset"), "reset", idle_digest, 1000000000, 2000000000, true),
    "cross-controller target rejected");
  check(!admit(std::string(513, 'x'), "reset", true), "bounded parser");
  const auto total_before = guard.rejected(), motion_before = guard.rejected_motion();
  check(!guard.accept("invalid", "stop", idle_digest, 1000000000, 2000000000, false, true),
    "invalid revocation still rejects and locks");
  check(guard.rejected() == total_before + 1 && guard.rejected_motion() == motion_before,
    "stop rejection counted separately from positive authority");
  check(!guard.fresh(1000000000, 2000000000), "invalid stop cannot unlock");
  check(!admit("invalid", "reset", true), "invalid reset still rejects");
  check(guard.rejected_motion() == motion_before + 1, "reset rejection is motion authority rejection");
  bool invalid = false;
  try {integer("9223372036854775808");} catch (const std::exception &) {invalid = true;}
  check(invalid, "integer overflow rejected");
  Grant goal_grant; goal_grant.sim = 1000000000;
  check(current_goal_stamp(0, goal_grant, 1000000000, 1049999999), "zero header binds fresh signed grant");
  check(!current_goal_stamp(0, goal_grant, 1000000000, 1050000000), "zero header cannot refresh stale grant");
  check(!current_goal_stamp(0, goal_grant, 1000000001, 1000000000), "zero header cannot bypass stop cutoff");
  check(!current_goal_stamp(0, Grant{}, 0, 1000000000), "zero header requires signed issue time");
  std::cout << "controller permit adversarial policy checks passed\n";
}
