#pragma once
#include <stddef.h>
#include <stdint.h>

// Append-only data-flash reservations. Never erase or fall back to an old epoch.
// A torn, non-erased word consumes a slot, even though that boot never actuated.
template <class Storage>
uint32_t reserve_boot_epoch(Storage& storage, size_t slots = 1024) {
  size_t first_blank = slots;
  for (size_t i = 0; i < slots; ++i) {
    uint32_t word;
    if (!storage.read(i * 4, word)) return 0;
    if (word == UINT32_MAX) {
      if (first_blank == slots) first_blank = i;
    } else if (first_blank != slots) {
      return 0;  // A hole is corruption, not permission to reuse an epoch.
    }
  }
  if (first_blank == slots || !storage.program_zero(first_blank * 4)) return 0;
  uint32_t word;
  if (!storage.read(first_blank * 4, word) || word != 0) return 0;
  return uint32_t(first_blank + 1);
}
