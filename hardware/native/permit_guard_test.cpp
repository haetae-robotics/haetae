#include "../uno_r4_permit/permit_guard.h"
#include "../uno_r4_permit/epoch.h"
#include <assert.h>
#include <array>
#include <string>

static uint32_t clock_value, clock_step;
static uint32_t clock_read() { uint32_t value = clock_value; clock_value += clock_step; return value; }
static const uint8_t install[16] = {1};
static const uint8_t challenge_key[32] = {2};
static uint8_t signing_key[64], public_key[32];
static uint8_t session_key[32];
static void send(PermitGuard& guard, const std::string& raw) {
  for (char c : raw + "\n") guard.feed(c, clock_value);
}
static std::string permit(PermitGuard& guard, const char* op = "RUN", uint32_t epoch = 0) {
  char response[160], id[33], nonce_hex[33], state[16], reason[24];
  unsigned generation, seq, token, stamp, response_epoch;
  guard.reply(response, sizeof(response));
  if (sscanf(response, "H2 OK %32s %u %u %u %u %u %32s %15s %23s", id, &response_epoch,
                &generation, &seq, &token, &stamp, nonce_hex, state, reason) != 9) {
    fprintf(stderr, "Unexpected test context: %s", response); abort();
  }
  uint8_t nonce[16], bytes[PermitGuard::bind_size], signature[64];
  for (size_t i = 0; i < 16; ++i) { unsigned n; sscanf(nonce_hex + i * 2, "%2x", &n); nonce[i] = uint8_t(n); }
  bool binding = !strcmp(op, "BIND");
  uint8_t ephemeral_private[32] = {4}, ephemeral[32], controller_private[32], controller[32], shared[32];
  if (binding) {
    crypto_x25519_public_key(ephemeral, ephemeral_private);
    static const uint8_t domain[] = "HAETAE-X25519-H2";
    crypto_blake2b_keyed(controller_private, 32, challenge_key, 32, domain, sizeof(domain)-1);
    crypto_x25519_public_key(controller, controller_private);
    PermitGuard::bind_payload(bytes, install, epoch ? epoch : response_epoch, generation, seq + 1, token, stamp, nonce, ephemeral, controller);
    crypto_ed25519_sign(signature, signing_key, bytes, PermitGuard::bind_size);
    crypto_x25519(shared, ephemeral_private, controller);
    crypto_blake2b_keyed(session_key, 32, shared, 32, bytes, PermitGuard::bind_size);
  } else {
    PermitGuard::payload(bytes, install, epoch ? epoch : response_epoch, generation, seq + 1, token, stamp, 200, !strcmp(op, "RUN"), nonce);
    crypto_blake2b_keyed(signature, 32, session_key, 32, bytes, PermitGuard::payload_size);
  }
  std::string sig; const char* digits = "0123456789abcdef";
  for (size_t i = 0; i < (binding ? 64 : 32); ++i) { sig += digits[signature[i] >> 4]; sig += digits[signature[i] & 15]; }
  std::string extra;
  if (binding) {
    for (uint8_t byte : ephemeral) { extra += digits[byte >> 4]; extra += digits[byte & 15]; }
    extra += " ";
  }
  return std::string("H2 ") + op + " " + std::to_string(generation) + " " + std::to_string(seq + 1) + " " +
         std::to_string(token) + " " + std::to_string(stamp) + (binding ? " 0 " : " 200 ") + extra + sig;
}
static void start(PermitGuard& guard, uint32_t now = 0) {
  clock_value = now; clock_step = 0;
  send(guard, "H2 HELLO"); send(guard, permit(guard, "BIND"));
  assert(!guard.on() && !guard.armed());
  send(guard, permit(guard, "ARM")); send(guard, permit(guard));
  assert(guard.on());
}
struct Storage {
  std::array<uint32_t, 4> words = {UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX};
  bool read_ok = true, write_ok = true;
  bool read(size_t offset, uint32_t& value) { value = words[offset/4]; return read_ok; }
  bool program_zero(size_t offset) { if (write_ok) words[offset/4] = 0; return write_ok; }
};
int main() {
  uint8_t seed[32] = {3}; crypto_ed25519_key_pair(signing_key, public_key, seed);
  PermitGuard guard(1, install, public_key, challenge_key, clock_read);
  start(guard); guard.tick(199); assert(guard.on()); guard.tick(200); assert(!guard.on());
  send(guard, "H2 STATUS"); send(guard, permit(guard)); assert(!guard.on());
  start(guard, 201); auto replay = permit(guard); send(guard, replay); assert(guard.on());
  send(guard, replay); assert(!guard.on());
  start(guard, 202); auto forged = permit(guard); forged.back() = forged.back() == '0' ? '1' : '0';
  send(guard, forged); assert(!guard.on() && !strcmp(guard.reason(), "signature"));
  start(guard, 203); auto changed_action = permit(guard); changed_action.replace(3, 3, "ARM");
  send(guard, changed_action); assert(!guard.on());
  start(guard, 204); send(guard, permit(guard, "RUN", 2)); assert(!guard.on());
  start(guard, 205); auto old_nonce = permit(guard); send(guard, "H2 STATUS");
  send(guard, old_nonce); assert(!guard.on());
  start(guard, 206); auto late = permit(guard); clock_step = 50;
  send(guard, late); assert(!guard.on() && !strcmp(guard.reason(), "stale")); clock_step = 0;
  start(guard, UINT32_MAX - 99); guard.tick(99); assert(guard.on()); guard.tick(100); assert(!guard.on());
  PermitGuard old_boot(1, install, public_key, challenge_key, clock_read), reboot(2, install, public_key, challenge_key, clock_read);
  start(old_boot, 0); auto across_boot = permit(old_boot); start(reboot, 0);
  send(reboot, across_boot); assert(!reboot.on() && !strcmp(reboot.reason(), "signature"));
  for (const std::string& invalid : {std::string("H1 RUN 0 1 1"), std::string("H2 RUN"),
       std::string("H2 RUN 1 3 3 0 200 ") + std::string(128, '0'), std::string(300, 'x')}) {
    start(guard); send(guard, invalid); assert(!guard.on());
  }
  start(guard); guard.feed('H', 1); guard.tick(101); assert(!guard.on());
  PermitGuard unprovisioned(0, install, public_key, challenge_key, clock_read);
  send(unprovisioned, "H2 HELLO"); assert(!unprovisioned.armed());
  PermitGuard invalid_bind(1, install, public_key, challenge_key, clock_read);
  clock_value = clock_step = 0;
  send(invalid_bind, "H2 HELLO");
  auto bad_certificate = permit(invalid_bind, "BIND");
  bad_certificate.back() = bad_certificate.back() == '0' ? '1' : '0';
  send(invalid_bind, bad_certificate);
  assert(!invalid_bind.on() && !invalid_bind.armed() && !strcmp(invalid_bind.reason(), "signature"));
  send(invalid_bind, "H2 HELLO");
  auto late_bind = permit(invalid_bind, "BIND"); clock_step = 500;
  send(invalid_bind, late_bind);
  assert(!invalid_bind.on() && !invalid_bind.armed() && !strcmp(invalid_bind.reason(), "stale"));
  clock_step = 0;
  send(invalid_bind, "H2 HELLO");
  // No BIND means a well-formed MAC frame still has no actuation authority.
  send(invalid_bind, permit(invalid_bind, "ARM"));
  assert(!invalid_bind.on() && !invalid_bind.armed() && !strcmp(invalid_bind.reason(), "unarmed"));
  Storage storage; assert(reserve_boot_epoch(storage, 4) == 1); assert(reserve_boot_epoch(storage, 4) == 2);
  storage.words[2] = 0xffff00ff; // Torn reservation is consumed, never reused.
  assert(reserve_boot_epoch(storage, 4) == 4); assert(reserve_boot_epoch(storage, 4) == 0);
  Storage hole; hole.words[1] = 0; assert(reserve_boot_epoch(hole, 4) == 0);
  Storage unreadable; unreadable.read_ok = false; assert(reserve_boot_epoch(unreadable, 4) == 0);
  Storage unwritable; unwritable.write_ok = false; assert(reserve_boot_epoch(unwritable, 4) == 0);
  puts("H2 Ed25519/X25519 binding, per-action MAC, epoch/nonce/replay/expiry/latch/rollover/parser and append-only epoch passed");
}
