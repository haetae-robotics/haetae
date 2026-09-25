use serde::{Deserialize, Serialize};

/// A point in the robot's 2D map frame, in metres.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Point2 {
    pub x: f64,
    pub y: f64,
}

impl Point2 {
    pub fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    pub fn is_finite(self) -> bool {
        self.x.is_finite() && self.y.is_finite()
    }

    pub fn dist(self, other: Point2) -> f64 {
        (self.x - other.x).hypot(self.y - other.y)
    }
}

/// Axis-aligned rectangle.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rect {
    pub min: Point2,
    pub max: Point2,
}

impl Rect {
    pub fn is_valid(&self) -> bool {
        self.min.is_finite()
            && self.max.is_finite()
            && self.min.x <= self.max.x
            && self.min.y <= self.max.y
    }

    pub fn contains(&self, p: Point2) -> bool {
        (self.min.x..=self.max.x).contains(&p.x) && (self.min.y..=self.max.y).contains(&p.y)
    }

    /// Whether the segment `a → b` touches the rectangle (Liang–Barsky clipping).
    pub fn intersects_segment(&self, a: Point2, b: Point2) -> bool {
        let (dx, dy) = (b.x - a.x, b.y - a.y);
        let mut t0 = 0.0_f64;
        let mut t1 = 1.0_f64;
        let edges = [
            (-dx, a.x - self.min.x),
            (dx, self.max.x - a.x),
            (-dy, a.y - self.min.y),
            (dy, self.max.y - a.y),
        ];
        for (p, q) in edges {
            if p == 0.0 {
                if q < 0.0 {
                    return false;
                }
                continue;
            }
            let t = q / p;
            if p < 0.0 {
                t0 = t0.max(t);
            } else {
                t1 = t1.min(t);
            }
            if t0 > t1 {
                return false;
            }
        }
        true
    }
}

/// Shortest distance from `p` to the segment `a → b`.
pub fn point_segment_distance(p: Point2, a: Point2, b: Point2) -> f64 {
    let (dx, dy) = (b.x - a.x, b.y - a.y);
    let len2 = dx * dx + dy * dy;
    if len2 == 0.0 {
        return p.dist(a);
    }
    let t = (((p.x - a.x) * dx + (p.y - a.y) * dy) / len2).clamp(0.0, 1.0);
    p.dist(Point2::new(a.x + t * dx, a.y + t * dy))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit() -> Rect {
        Rect {
            min: Point2::new(0.0, 0.0),
            max: Point2::new(1.0, 1.0),
        }
    }

    #[test]
    fn segment_crossing_rect_without_endpoints_inside() {
        assert!(unit().intersects_segment(Point2::new(-1.0, 0.5), Point2::new(2.0, 0.5)));
    }

    #[test]
    fn segment_missing_rect() {
        assert!(!unit().intersects_segment(Point2::new(-1.0, 2.0), Point2::new(2.0, 2.0)));
        assert!(!unit().intersects_segment(Point2::new(2.0, -1.0), Point2::new(2.0, 3.0)));
    }

    #[test]
    fn degenerate_segment_is_a_point() {
        assert!(unit().intersects_segment(Point2::new(0.5, 0.5), Point2::new(0.5, 0.5)));
        assert!(!unit().intersects_segment(Point2::new(5.0, 5.0), Point2::new(5.0, 5.0)));
    }

    #[test]
    fn distance_to_segment() {
        let (a, b) = (Point2::new(0.0, 0.0), Point2::new(10.0, 0.0));
        assert_eq!(point_segment_distance(Point2::new(5.0, 3.0), a, b), 3.0);
        assert_eq!(point_segment_distance(Point2::new(-4.0, 3.0), a, b), 5.0);
    }
}
