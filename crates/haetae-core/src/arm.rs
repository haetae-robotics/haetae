//! Joint-space checks for short, monitored arm command chunks.

use crate::{Arm, JointWaypoint, WorldSnapshot};

pub(crate) fn check_trajectory(
    arm: &Arm,
    world: &WorldSnapshot,
    points: &[JointWaypoint],
    ttl_ms: u64,
) -> Result<(), &'static str> {
    if ttl_ms == 0
        || ttl_ms > arm.max_duration_ms
        || points.len() < 3
        || points.len() > arm.max_points
    {
        return Err("envelope:arm-duration");
    }
    let measured = world.robot.joints.as_ref().ok_or("invalid:arm-world")?;
    if measured.len() != arm.joints.len()
        || measured
            .iter()
            .zip(&arm.joints)
            .any(|(m, j)| m.name != j.name)
    {
        return Err("invalid:arm-world");
    }
    if points[0].time_from_start_ms != 0
        || points.last().is_some_and(|p| p.time_from_start_ms > ttl_ms)
    {
        return Err("envelope:arm-time");
    }
    let count = arm.joints.len();
    if points.iter().any(|p| p.positions.len() != count) {
        return Err("invalid:arm-points");
    }
    for (i, (m, joint)) in measured.iter().zip(&arm.joints).enumerate() {
        if (points[0].positions[i] - m.position).abs() > arm.max_start_error {
            return Err("envelope:arm-start");
        }
        for p in points {
            if !p.positions[i].is_finite()
                || p.positions[i] < joint.min_position
                || p.positions[i] > joint.max_position
            {
                return Err("envelope:arm-position");
            }
        }
        let mut previous_speed = m.velocity;
        for pair in points.windows(2) {
            let dt_ms = pair[1]
                .time_from_start_ms
                .checked_sub(pair[0].time_from_start_ms)
                .ok_or("envelope:arm-time")?;
            if dt_ms == 0 {
                return Err("envelope:arm-time");
            }
            let dt = dt_ms as f64 / 1000.0;
            let speed = (pair[1].positions[i] - pair[0].positions[i]) / dt;
            if speed.abs() > joint.max_velocity + 1e-9 {
                return Err("envelope:arm-velocity");
            }
            if (speed - previous_speed).abs() / dt > joint.max_acceleration + 1e-9 {
                return Err("envelope:arm-acceleration");
            }
            previous_speed = speed;
        }
        if previous_speed.abs() > 1e-9 {
            return Err("envelope:arm-final-motion");
        }
    }
    Ok(())
}
