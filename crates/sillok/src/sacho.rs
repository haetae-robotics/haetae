//! Sacho (사초, "rough draft"): the pre-trigger ring buffer.

use std::collections::VecDeque;

use serde_json::Value;

use crate::writer::SillokWriter;
use crate::Result;

/// A buffered record; becomes an [`crate::Entry`] when flushed.
#[derive(Debug)]
struct Pending {
    ts_ms: u64,
    kind: String,
    payload: Value,
}

/// Fixed-capacity in-memory ring buffer for the pre-incident window.
///
/// Push every observed record into the sacho; when an incident fires,
/// [`Sacho::flush_into`] drains the retained tail into the real log, oldest
/// first. Pushing onto a full buffer drops the oldest record.
pub struct Sacho {
    capacity: usize,
    buf: VecDeque<Pending>,
}

impl Sacho {
    /// A ring holding at most `capacity` records. `Sacho::new(0)` is legal
    /// and drops everything pushed.
    pub fn new(capacity: usize) -> Self {
        Sacho {
            capacity,
            buf: VecDeque::new(),
        }
    }

    /// Buffer one record, evicting the oldest if the ring is full.
    pub fn push(&mut self, ts_ms: u64, kind: &str, payload: Value) {
        if self.capacity == 0 {
            return;
        }
        if self.buf.len() == self.capacity {
            self.buf.pop_front();
        }
        self.buf.push_back(Pending {
            ts_ms,
            kind: kind.to_owned(),
            payload,
        });
    }

    /// Drain every buffered record into `w`, oldest first; returns the number
    /// appended. If an append fails, the record it failed on and everything
    /// after it stay buffered — nothing is lost but what was written.
    pub fn flush_into(&mut self, w: &mut SillokWriter) -> Result<usize> {
        let mut n = 0;
        // Clone rather than take: if append fails, the record must stay intact.
        while let Some(p) = self.buf.front() {
            w.append(p.ts_ms, &p.kind, p.payload.clone())?;
            self.buf.pop_front();
            n += 1;
        }
        Ok(n)
    }

    /// Records currently buffered.
    pub fn len(&self) -> usize {
        self.buf.len()
    }

    /// Whether nothing is buffered.
    pub fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }

    /// Maximum records retained.
    pub fn capacity(&self) -> usize {
        self.capacity
    }
}
