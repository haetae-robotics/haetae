#include <WDT.h>
#include "guard.h"

BenchGuard guard;
bool watchdog_ready = false;

void setup() {
  // Only the built-in LED is driven. No motor/relay pins are assigned.
  digitalWrite(LED_BUILTIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  watchdog_ready = WDT.begin(500) != 0;
  if (!watchdog_ready) guard.fail("watchdog");
}

void loop() {
  if (!watchdog_ready) {
    digitalWrite(LED_BUILTIN, LOW);
    return;
  }
  guard.tick(millis());
  digitalWrite(LED_BUILTIN, guard.on() ? HIGH : LOW);
  // RX floods cannot defer the next lease check indefinitely.
  for (int budget = 0; budget < 32 && Serial.available(); ++budget) {
    bool ready = guard.feed(char(Serial.read()), millis());
    digitalWrite(LED_BUILTIN, guard.on() ? HIGH : LOW);
    if (ready) {
      char response[96];
      size_t length = guard.reply(response, sizeof(response));
      // The pinned USB implementation can block if its TX buffer is full.
      if (!length || Serial.availableForWrite() < int(length)) {
        guard.fail("tx");
        digitalWrite(LED_BUILTIN, LOW);
      } else if (Serial.write(reinterpret_cast<const uint8_t*>(response), length) != length) {
        guard.fail("tx");
        digitalWrite(LED_BUILTIN, LOW);
      }
    }
  }
  WDT.refresh();
}
