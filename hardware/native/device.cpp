// Process emulator for the SAME guard as the sketch. No electrical/USB/WDT proof.
#include "../uno_r4_bench/guard.h"
#include <chrono>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  FILE* audit = fopen(argv[1], "w");
  if (!audit) return 2;
  setvbuf(audit, nullptr, _IONBF, 0);
  fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
  fcntl(STDOUT_FILENO, F_SETFL, O_NONBLOCK);
  auto origin = std::chrono::steady_clock::now();
  BenchGuard guard;
  bool previous = true;
  uint32_t previous_seq = UINT32_MAX;
  const char* previous_reason = "";
  for (;;) {
    auto now64 = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - origin).count();
    uint32_t now = uint32_t(now64);
    guard.tick(now);
    if (previous != guard.on() || strcmp(previous_reason, guard.reason()) || previous_seq != guard.sequence()) {
      fprintf(audit, "{\"device_ms\":%lld,\"on\":%s,\"reason\":\"%s\",\"sequence\":%lu}\n",
              static_cast<long long>(now64), guard.on() ? "true" : "false", guard.reason(),
              static_cast<unsigned long>(guard.sequence()));
      previous = guard.on();
      previous_seq = guard.sequence();
      previous_reason = guard.reason();
    }
    struct pollfd fd = {STDIN_FILENO, POLLIN, 0};
    if (poll(&fd, 1, 1) < 0 && errno != EINTR) return 3;
    if (!(fd.revents & POLLIN)) continue;
    char input[32];
    ssize_t n = read(STDIN_FILENO, input, sizeof(input));
    if (n <= 0) continue; // Keep ticking after host close/kill.
    for (ssize_t i = 0; i < n; ++i) {
      if (guard.feed(input[i], now)) {
        char reply[96];
        size_t length = guard.reply(reply, sizeof(reply));
        if (!length || write(STDOUT_FILENO, reply, length) != ssize_t(length)) guard.fail("tx");
      }
    }
  }
}
