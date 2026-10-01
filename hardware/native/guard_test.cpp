#include "../uno_r4_bench/guard.h"
#include <assert.h>
#include <string>

static void send(BenchGuard& g, const std::string& line, uint32_t now) {
  for (char c : line + "\n") g.feed(c, now);
}
static void start(BenchGuard& g, uint32_t now) {
  send(g, "H1 HELLO 0123456789abcdef 0 0", now);
  send(g, "H1 ARM 0123456789abcdef 1 1", now);
  send(g, "H1 RUN 0123456789abcdef 2 2", now);
  assert(g.on());
}
int main() {
  BenchGuard g;
  assert(!g.on() && !g.armed());
  start(g, 100);
  g.tick(299); assert(g.on());
  g.tick(300); assert(!g.on() && !g.armed());
  send(g, "H1 RUN 0123456789abcdef 3 3", 300);
  assert(!g.on());
  send(g, "H1 HELLO fedcba9876543210 0 0", 300);
  send(g, "H1 ARM fedcba9876543210 1 1", 300);
  send(g, "H1 RUN fedcba9876543210 2 2", 300);
  assert(g.on());
  send(g, "H1 RUN fedcba9876543210 2 2", 301);
  assert(!g.on()); // Replay removes output.

  BenchGuard stale;
  start(stale, 0);
  send(stale, "H1 RUN 0123456789abcdef 3 3", 100);
  assert(!stale.on() && !strcmp(stale.reason(), "stale"));
  BenchGuard status;
  start(status, 0);
  for (uint32_t i = 1; i <= 4; ++i) {
    send(status, "H1 STATUS 0123456789abcdef " + std::to_string(i + 2) + " " +
         std::to_string(i + 2), i * 50);
  }
  assert(!status.on() && !status.armed()); // STATUS never extends lease.
  BenchGuard polling;
  start(polling, 0);
  send(polling, "H1 STATUS 0123456789abcdef 3 3", 150);
  assert(polling.on()); // Non-actuating poll can acquire a fresh challenge.
  send(polling, "H1 RUN 0123456789abcdef 4 4", 151);
  assert(polling.on());
  send(polling, "H1 RUN 0123456789abcdef 5 5", 251);
  assert(!polling.on() && !strcmp(polling.reason(), "stale"));

  BenchGuard wrap;
  start(wrap, UINT32_MAX - 99);
  wrap.tick(99); assert(wrap.on());
  wrap.tick(100); assert(!wrap.on());

  const std::string bad_cases[] = {"", "H1 RUN 0123456789abcdef 3 3 extra",
       "H1 RUN 0123456789abcdef 4294967296 3", "H1 RUN 0123456789abcdef -1 3",
       std::string(200, 'x'), "H1 RUN 0123456789abcdeg 3 3"};
  for (const std::string& bad : bad_cases) {
    BenchGuard invalid;
    start(invalid, 0);
    send(invalid, bad, 1);
    assert(!invalid.on());
  }
  BenchGuard partial;
  start(partial, 0);
  partial.feed('H', 1);
  partial.tick(101);
  assert(!partial.on() && !strcmp(partial.reason(), "partial"));
  send(partial, "1 RUN 0123456789abcdef 3 3", 102);
  assert(!partial.on());

  BenchGuard stop;
  start(stop, 0);
  send(stop, "H1 STOP 0123456789abcdef 3 3", 150);
  assert(!stop.on());
  send(stop, "H1 ARM 0123456789abcdef 4 4", 151);
  assert(!stop.on() && !stop.armed());
  puts("guard: deadline, latch, replay, challenge, rollover, parser, partial, STOP passed");
}
