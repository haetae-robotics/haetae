//! Bounded replay detection: the `(source, id)` pairs already seen.

use std::collections::{HashMap, HashSet, VecDeque};

use haetae_core::Source;

/// A bounded set of seen proposal identities with FIFO eviction.
///
/// `Source` is part of the key: proposal ids are only unique per source, so
/// `(vla, 7)` and `(planner, 7)` are different proposals.
///
/// Ids are expected to increase per source. When a key is evicted, its id
/// becomes that source's floor, and any id at or below the floor counts as a
/// replay. Without the floor an attacker could flush the set with fresh ids
/// and then replay an old proposal (its claimed timestamp is attacker-chosen,
/// so `stale:proposal` would not catch it).
pub(crate) struct Dedup {
    capacity: usize,
    /// Insertion order, oldest first — the eviction queue.
    order: VecDeque<(Source, u64)>,
    seen: HashSet<(Source, u64)>,
    /// Highest evicted id per source.
    floor: HashMap<Source, u64>,
}

impl Dedup {
    pub(crate) fn new(capacity: usize) -> Self {
        Dedup {
            capacity,
            order: VecDeque::new(),
            seen: HashSet::new(),
            floor: HashMap::new(),
        }
    }

    /// `false` the first time `key` is seen (and records it); `true` on a
    /// repeat, or when the id is at or below its source's eviction floor.
    /// A repeat does not refresh the key's position in the queue.
    pub(crate) fn check_and_insert(&mut self, key: (Source, u64)) -> bool {
        if self.floor.get(&key.0).is_some_and(|&floor| key.1 <= floor) {
            return true;
        }
        if !self.seen.insert(key) {
            return true;
        }
        self.order.push_back(key);
        while self.order.len() > self.capacity {
            if let Some(old) = self.order.pop_front() {
                self.seen.remove(&old);
                let floor = self.floor.entry(old.0).or_insert(old.1);
                *floor = (*floor).max(old.1);
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeat_is_detected_and_oldest_is_evicted() {
        let mut d = Dedup::new(2);
        assert!(!d.check_and_insert((Source::Vla, 1)));
        assert!(d.check_and_insert((Source::Vla, 1)));
        // a repeat does not refresh the key's position
        assert!(d.check_and_insert((Source::Vla, 1)));
        // same id, different source: not a replay
        assert!(!d.check_and_insert((Source::Planner, 1)));
        // capacity 2: a third key evicts (vla, 1), which becomes vla's floor
        assert!(!d.check_and_insert((Source::Vla, 2)));
        assert!(d.check_and_insert((Source::Vla, 1)));
    }

    #[test]
    fn flushing_the_set_does_not_reopen_old_ids() {
        let mut d = Dedup::new(4);
        assert!(!d.check_and_insert((Source::Vla, 7)));
        for id in 100..200 {
            assert!(!d.check_and_insert((Source::Vla, id)));
        }
        assert!(
            d.check_and_insert((Source::Vla, 7)),
            "old id replayed after flush"
        );
        assert!(
            d.check_and_insert((Source::Vla, 150)),
            "evicted id replayed"
        );
        assert!(!d.check_and_insert((Source::Vla, 200)));
        // Other sources are unaffected.
        assert!(!d.check_and_insert((Source::Planner, 7)));
    }
}
