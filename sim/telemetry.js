/** Reject incomplete/nonfinite measurements before mutating display state. */
export function validTelemetry(row) {
  const point = (value) => value && Number.isFinite(value.x) && Number.isFinite(value.y);
  const points = (values) => Array.isArray(values) && values.length <= 1024 &&
    values.every((value) => point(value?.pos));
  return row && ['x', 'y', 'speed', 'joint', 'sim_ms'].every((key) => Number.isFinite(row[key])) &&
    (row.yaw === undefined || Number.isFinite(row.yaw)) && points(row.humans) &&
    (row.detections === undefined || points(row.detections)) &&
    (row.joints === undefined || (Array.isArray(row.joints) && row.joints.length <= 128 &&
      row.joints.every((joint) => typeof joint?.name === 'string' &&
        Number.isFinite(joint.position) && Number.isFinite(joint.velocity)))) &&
    (row.human_motion == null || (row.human_motion &&
      Number.isFinite(row.human_motion.distance_m) && Number.isFinite(row.human_motion.heading)));
}
