//! Conservative 2D disc sweep for a differential base.

use crate::{Base, Point2, Rect, WorldSnapshot};

pub(crate) struct SweptPath {
    pub points: Vec<Point2>,
    pub inflation: f64,
}

impl SweptPath {
    pub fn inside(&self, workspace: &Rect) -> bool {
        self.points.iter().all(|p| {
            p.x - self.inflation >= workspace.min.x
                && p.x + self.inflation <= workspace.max.x
                && p.y - self.inflation >= workspace.min.y
                && p.y + self.inflation <= workspace.max.y
        })
    }

    pub fn intersects(&self, rect: &Rect) -> bool {
        let expanded = Rect {
            min: Point2::new(rect.min.x - self.inflation, rect.min.y - self.inflation),
            max: Point2::new(rect.max.x + self.inflation, rect.max.y + self.inflation),
        };
        self.points
            .windows(2)
            .any(|p| expanded.intersects_segment(p[0], p[1]))
    }
}

pub(crate) fn swept_path(
    world: &WorldSnapshot,
    yaw: f64,
    linear: f64,
    angular: f64,
    ttl_ms: u64,
    now_ms: u64,
    base: &Base,
) -> Result<SweptPath, &'static str> {
    let age = now_ms.saturating_sub(world.stamp_ms) as f64 / 1000.0;
    let horizon = age
        + base.latency_ms as f64 / 1000.0
        + ttl_ms as f64 / 1000.0
        + linear.abs() / base.max_decel;
    let distance = linear.abs() * horizon;
    // Fixed work budget. Long or tiny-radius sweeps fail closed; silently
    // widening the sampling interval would miss a narrow exclusion zone.
    let intervals = ((distance / (base.footprint_radius / 2.0)).ceil() as usize).max(1);
    if intervals > 4096 || !distance.is_finite() || !horizon.is_finite() {
        return Err("envelope:sweep-budget");
    }
    let mut points = Vec::with_capacity(intervals + 1);
    let origin = world.robot.pose;
    for i in 0..=intervals {
        let t = horizon * i as f64 / intervals as f64;
        let heading = yaw + angular * t;
        let p = if angular.abs() < 1e-9 {
            Point2::new(
                origin.x + linear * t * yaw.cos(),
                origin.y + linear * t * yaw.sin(),
            )
        } else {
            let radius = linear / angular;
            Point2::new(
                origin.x + radius * (heading.sin() - yaw.sin()),
                origin.y - radius * (heading.cos() - yaw.cos()),
            )
        };
        if !p.is_finite() {
            return Err("envelope:sweep-budget");
        }
        points.push(p);
    }
    let angle_per_interval = angular.abs() * horizon / intervals as f64;
    let sagitta = if angular.abs() < 1e-9 {
        0.0
    } else {
        (linear / angular).abs() * (1.0 - (angle_per_interval / 2.0).cos()).abs()
    };
    let measured = world.robot.twist.map_or(0.0, |twist| {
        twist.linear.abs() * age + distance * (twist.angular.abs() * age).min(2.0)
    });
    let inflation = base.footprint_radius + measured + sagitta;
    if !inflation.is_finite() {
        return Err("envelope:sweep-budget");
    }
    Ok(SweptPath { points, inflation })
}
