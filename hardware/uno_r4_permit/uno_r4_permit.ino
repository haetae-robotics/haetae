#include <WDT.h>
#include <r_flash_lp.h>
#include "provision.h"  // Device-private challenge key plus pinned public authority.
#include "epoch.h"
#include "permit_guard.h"

struct BootFlash {
  flash_lp_instance_ctrl_t ctrl = {};
  flash_cfg_t cfg = {};
  bool open() {
    cfg.ipl = cfg.err_ipl = BSP_IRQ_DISABLED;
    cfg.irq = cfg.err_irq = FSP_INVALID_VECTOR;
    return R_FLASH_LP_Open(&ctrl, &cfg) == FSP_SUCCESS;
  }
  bool read(size_t offset, uint32_t& word) {
    if (offset >= 4096 || offset % 4) return false;
    word = *reinterpret_cast<volatile const uint32_t*>(FLASH_BASE_ADDRESS + offset);
    return true;
  }
  bool program_zero(size_t offset) {
    if (offset >= 4096 || offset % 4) return false;
    const uint32_t zero = 0;
    return R_FLASH_LP_Write(&ctrl, reinterpret_cast<uint32_t>(&zero),
                            FLASH_BASE_ADDRESS + offset, sizeof(zero)) == FSP_SUCCESS;
  }
};
PermitGuard* guard;
bool watchdog_ready;

void setup() {
  digitalWrite(LED_BUILTIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);
  // Reserve durably before exposing any challenge or positive command.
  BootFlash flash;
  uint32_t epoch = flash.open() ? reserve_boot_epoch(flash) : 0;
  R_FLASH_LP_Close(&flash.ctrl);
  static PermitGuard instance(epoch, HAETAE_INSTALL, HAETAE_PUBLIC_KEY, HAETAE_CHALLENGE_KEY, millis);
  guard = &instance;
  Serial.begin(115200);
  watchdog_ready = WDT.begin(500) != 0;
  if (!watchdog_ready) guard->fail("watchdog");
}
void loop() {
  if (!watchdog_ready) { digitalWrite(LED_BUILTIN, LOW); return; }
  guard->tick(millis());
  digitalWrite(LED_BUILTIN, guard->on() ? HIGH : LOW);
  for (int budget = 0; budget < 32 && Serial.available(); ++budget) {
    char c = char(Serial.read());
    // Crypto runs only after LF. The physical output is already OFF while the
    // verifier blocks; safety timing never relies on finishing a hostile check.
    if (c == '\n' && guard->verifying_frame()) digitalWrite(LED_BUILTIN, LOW);
    bool reply_ready = guard->feed(c, millis());
    digitalWrite(LED_BUILTIN, guard->on() ? HIGH : LOW);
    if (reply_ready) {
      char response[160];
      size_t length = guard->reply(response, sizeof(response));
      if (!length || Serial.availableForWrite() < int(length) ||
          Serial.write(reinterpret_cast<const uint8_t*>(response), length) != length) {
        guard->fail("tx"); digitalWrite(LED_BUILTIN, LOW);
      }
    }
  }
  WDT.refresh();
}
