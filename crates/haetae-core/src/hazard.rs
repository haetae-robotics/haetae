//! Conservative swept end-effector sphere checks for a trusted motion adapter.
//!
//! Paths must be derived from the exact joint plan by that adapter. This module
//! is not a raw-joint enforcer, perception system, or certified safety function.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Point3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}
impl Point3 {
    fn values(self) -> [f64; 3] {
        [self.x, self.y, self.z]
    }
    fn valid(self) -> bool {
        self.values()
            .iter()
            .all(|v| v.is_finite() && v.abs() <= 100.0)
    }
    fn distance(self, b: Self) -> f64 {
        self.values()
            .iter()
            .zip(b.values())
            .map(|(a, b)| (a - b).powi(2))
            .sum::<f64>()
            .sqrt()
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Bounds3 {
    pub min: Point3,
    pub max: Point3,
}
impl Bounds3 {
    fn valid(self) -> bool {
        self.min.valid()
            && self.max.valid()
            && self
                .min
                .values()
                .iter()
                .zip(self.max.values())
                .all(|(a, b)| *a <= b)
    }
    fn intersects(self, a: Point3, b: Point3, margin: f64) -> bool {
        let mut lo: f64 = 0.0;
        let mut hi: f64 = 1.0;
        for ((a, b), (min, max)) in a
            .values()
            .iter()
            .zip(b.values())
            .zip(self.min.values().iter().zip(self.max.values()))
        {
            let delta = b - a;
            let min = min - margin;
            let max = max + margin;
            if delta.abs() < 1e-12 {
                if *a < min || *a > max {
                    return false;
                }
            } else {
                let p = (min - a) / delta;
                let q = (max - a) / delta;
                lo = lo.max(p.min(q));
                hi = hi.min(p.max(q));
                if lo > hi {
                    return false;
                }
            }
        }
        true
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemKind {
    Knife,
    Pressurized,
    Flammable,
    Conductive,
    Battery,
    Electrical,
    Bleach,
    Ammonia,
    Inert,
    Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RegionKind {
    Human,
    Heat,
    Electrical,
    Water,
    Container,
    Fall,
    Surface,
    Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Active,
    Inactive,
    Unknown,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Region {
    pub id: String,
    pub kind: RegionKind,
    pub state: State,
    pub bounds: Bounds3,
    /// Trusted observed/committed contents, including earlier accepted transfers.
    pub contents: Vec<ItemKind>,
    pub contents_known: bool,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HazardRequest {
    pub now_ms: u64,
    pub observed_ms: u64,
    pub confidence: f64,
    pub coverage_known: bool,
    pub item: ItemKind,
    pub observed_start: Point3,
    /// Tool/item envelope plus adapter-derived tracking and interpolation margin.
    pub swept_radius_m: f64,
    pub path: Vec<Point3>,
    pub regions: Vec<Region>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HazardDecision {
    pub allowed: bool,
    pub reason: String,
}
fn deny(reason: &str) -> HazardDecision {
    HazardDecision {
        allowed: false,
        reason: reason.into(),
    }
}

pub fn judge(request: &HazardRequest) -> HazardDecision {
    let r = request;
    if !r.coverage_known {
        return deny("perception:coverage-unknown");
    }
    if r.observed_ms > r.now_ms || r.now_ms - r.observed_ms > 200 {
        return deny("perception:stale");
    }
    if !r.confidence.is_finite() || !(0.9..=1.0).contains(&r.confidence) {
        return deny("perception:uncertain");
    }
    if r.item == ItemKind::Unknown {
        return deny("item:unknown");
    }
    if !r.observed_start.valid()
        || !r.swept_radius_m.is_finite()
        || !(0.01..=0.5).contains(&r.swept_radius_m)
        || !(2..=4096).contains(&r.path.len())
        || !r.path.iter().all(|p| p.valid())
        || r.regions.len() > 128
        || r.path[0].distance(r.observed_start) > 0.01
    {
        return deny("plan:invalid");
    }
    let mut ids = std::collections::HashSet::new();
    for region in &r.regions {
        if region.id.is_empty()
            || region.id.len() > 64
            || !ids.insert(&region.id)
            || !region.bounds.valid()
            || region.contents.len() > 32
        {
            return deny("scene:invalid");
        }
        let touches = r
            .path
            .windows(2)
            .any(|p| region.bounds.intersects(p[0], p[1], r.swept_radius_m));
        if !touches {
            continue;
        }
        if region.kind == RegionKind::Unknown || region.state == State::Unknown {
            return deny("region:unknown");
        }
        // Occupied human and fall volumes are always excluded, even if a
        // proposal calls itself a harmless transport or labels the device off.
        if region.kind == RegionKind::Human {
            return deny("human:protected-volume");
        }
        if region.kind == RegionKind::Fall {
            return deny("fall:protected-volume");
        }
        if region.state == State::Inactive
            && matches!(region.kind, RegionKind::Heat | RegionKind::Electrical)
        {
            continue;
        }
        match region.kind {
            RegionKind::Heat
                if matches!(
                    r.item,
                    ItemKind::Pressurized | ItemKind::Flammable | ItemKind::Battery
                ) =>
            {
                return deny("heat:hazardous-item")
            }
            RegionKind::Electrical
                if matches!(
                    r.item,
                    ItemKind::Conductive | ItemKind::Knife | ItemKind::Electrical
                ) =>
            {
                return deny("electricity:contact")
            }
            RegionKind::Water if matches!(r.item, ItemKind::Battery | ItemKind::Electrical) => {
                return deny("water:electrical-item")
            }
            RegionKind::Container => {
                if !region.contents_known || region.contents.contains(&ItemKind::Unknown) {
                    return deny("container:unknown-contents");
                }
                if (r.item == ItemKind::Bleach && region.contents.contains(&ItemKind::Ammonia))
                    || (r.item == ItemKind::Ammonia && region.contents.contains(&ItemKind::Bleach))
                {
                    return deny("chemicals:incompatible");
                }
            }
            _ => (),
        }
    }
    HazardDecision {
        allowed: true,
        reason: "plan:allowed".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn point(x: f64) -> Point3 {
        Point3 { x, y: 0., z: 0. }
    }
    fn request(item: ItemKind, kind: RegionKind) -> HazardRequest {
        HazardRequest {
            now_ms: 1000,
            observed_ms: 1000,
            confidence: 1.,
            coverage_known: true,
            item,
            observed_start: point(-1.),
            swept_radius_m: 0.05,
            path: vec![point(-1.), point(1.)],
            regions: vec![Region {
                id: "target".into(),
                kind,
                state: State::Active,
                bounds: Bounds3 {
                    min: point(-0.1),
                    max: point(0.1),
                },
                contents: vec![],
                contents_known: true,
            }],
        }
    }
    #[test]
    fn six_hazards_and_controls() {
        for (item, kind) in [
            (ItemKind::Knife, RegionKind::Human),
            (ItemKind::Pressurized, RegionKind::Heat),
            (ItemKind::Conductive, RegionKind::Electrical),
            (ItemKind::Battery, RegionKind::Water),
            (ItemKind::Ammonia, RegionKind::Container),
            (ItemKind::Inert, RegionKind::Fall),
        ] {
            let mut r = request(item, kind);
            if kind == RegionKind::Container {
                r.regions[0].contents.push(ItemKind::Bleach);
            }
            assert!(!judge(&r).allowed, "{item:?}/{kind:?}");
            r.regions[0].kind = RegionKind::Surface;
            assert!(judge(&r).allowed);
        }
    }
    #[test]
    fn missing_stale_and_invalid_facts() {
        let mut r = request(ItemKind::Inert, RegionKind::Surface);
        r.observed_ms = 799;
        assert!(!judge(&r).allowed);
        r.observed_ms = 1001;
        assert!(!judge(&r).allowed);
        r.observed_ms = 1000;
        r.item = ItemKind::Unknown;
        assert!(!judge(&r).allowed);
        r.item = ItemKind::Inert;
        r.confidence = f64::NAN;
        assert!(!judge(&r).allowed);
        r.confidence = 1.;
        r.regions[0].state = State::Unknown;
        assert!(!judge(&r).allowed);
        r.regions[0].state = State::Active;
        r.path[0] = point(-0.5);
        assert!(!judge(&r).allowed);
        r.path[0] = point(-1.);
        r.regions.push(r.regions[0].clone());
        assert!(!judge(&r).allowed);
    }
    #[test]
    fn inactive_does_not_disable_contents_or_water() {
        let mut r = request(ItemKind::Ammonia, RegionKind::Container);
        r.regions[0].contents.push(ItemKind::Bleach);
        r.regions[0].state = State::Inactive;
        assert!(!judge(&r).allowed);
        r = request(ItemKind::Battery, RegionKind::Water);
        r.regions[0].state = State::Inactive;
        assert!(!judge(&r).allowed);
        r = request(ItemKind::Conductive, RegionKind::Electrical);
        r.regions[0].state = State::Inactive;
        assert!(judge(&r).allowed);
    }
    #[test]
    fn normal_route_keeps_the_dangerous_region() {
        let mut r = request(ItemKind::Pressurized, RegionKind::Heat);
        r.path = vec![point(-1.), point(-0.5)];
        assert!(judge(&r).allowed);
        r.coverage_known = false;
        assert!(!judge(&r).allowed);
    }
    #[test]
    fn malformed_geometry_is_denied() {
        let mut r = request(ItemKind::Inert, RegionKind::Surface);
        r.path[1].z = f64::INFINITY;
        assert!(!judge(&r).allowed);
        r.path[1] = point(1.);
        r.regions[0].bounds.min = point(2.);
        assert!(!judge(&r).allowed);
        r.regions[0].bounds.min = point(-0.1);
        r.path = vec![point(-1.)];
        assert!(!judge(&r).allowed);
        r.path = vec![point(-1.); 4097];
        assert!(!judge(&r).allowed);
    }
    #[test]
    fn sequence_retains_container_contents() {
        let mut r = request(ItemKind::Bleach, RegionKind::Container);
        assert!(judge(&r).allowed);
        r.regions[0].contents.push(ItemKind::Bleach);
        r.item = ItemKind::Ammonia;
        assert_eq!(judge(&r).reason, "chemicals:incompatible");
        r.regions[0].contents_known = false;
        assert!(!judge(&r).allowed);
    }
    #[test]
    fn checks_between_waypoints_and_tool_radius() {
        let mut r = request(ItemKind::Knife, RegionKind::Human);
        assert!(!judge(&r).allowed);
        r.regions[0].bounds.min.y = 0.04;
        r.regions[0].bounds.max.y = 0.06;
        assert!(!judge(&r).allowed);
        r.regions[0].bounds.min.y = 0.06;
        assert!(judge(&r).allowed);
    }
}
