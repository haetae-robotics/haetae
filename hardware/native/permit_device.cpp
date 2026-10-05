// Process emulator for the identical guard. No GPIO/USB/WDT/flash timing proof.
#include "../uno_r4_permit/permit_guard.h"
#include <chrono>
#include <stdlib.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

static auto origin = std::chrono::steady_clock::now();
static uint32_t now_ms() {
  return uint32_t(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - origin).count());
}
static bool unhex(const char* s, uint8_t* out, size_t size) {
  if (strlen(s) != size * 2) return false;
  for (size_t i = 0; i < size; ++i) {
    unsigned byte;
    if (sscanf(s + i * 2, "%2x", &byte) != 1) return false;
    out[i] = uint8_t(byte);
  }
  return true;
}
int main(int argc, char** argv) {
  if (argc != 6) return 2;
  FILE* audit = fopen(argv[1], "w");
  FILE* seed = fopen(argv[4], "rb");
  uint8_t install[16], key[32], challenge_key[32];
  if (!audit || !seed || fread(challenge_key, 1, 32, seed) != 32 ||
      !unhex(argv[2], install, 16) || !unhex(argv[3], key, 32)) return 2;
  fclose(seed);
  unsigned long epoch = strtoul(argv[5], nullptr, 10);
  if (!epoch || epoch > UINT32_MAX) return 2;
  setvbuf(audit, nullptr, _IONBF, 0);
  fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
  fcntl(STDOUT_FILENO, F_SETFL, O_NONBLOCK);
  PermitGuard guard(uint32_t(epoch), install, key, challenge_key, now_ms);
  bool previous = true;
  uint32_t previous_seq = UINT32_MAX;
  const char* previous_reason = "";
  for (;;) {
    uint32_t now = now_ms();
    guard.tick(now);
    if (previous != guard.on() || previous_seq != guard.sequence() || strcmp(previous_reason, guard.reason())) {
      fprintf(audit, "{\"device_ms\":%lu,\"on\":%s,\"reason\":\"%s\",\"sequence\":%lu,\"renewed_ms\":%lu,\"verification_ms\":%lu}\n",
              (unsigned long)now, guard.on() ? "true" : "false", guard.reason(),
              (unsigned long)guard.sequence(), (unsigned long)guard.renewed_at(), (unsigned long)guard.verification_ms());
      previous = guard.on(); previous_seq = guard.sequence(); previous_reason = guard.reason();
    }
    struct pollfd fd = {STDIN_FILENO, POLLIN, 0};
    if (poll(&fd, 1, 1) < 0) return 2;
    if (fd.revents & POLLHUP) return 0;
    for (int budget = 0; budget < 32; ++budget) {
      char c;
      if (read(STDIN_FILENO, &c, 1) != 1) break;
      if (guard.feed(c, now_ms())) {
        char response[160]; size_t size = guard.reply(response, sizeof(response));
        if (!size || write(STDOUT_FILENO, response, size) != ssize_t(size)) guard.fail("tx");
      }
    }
  }
}
