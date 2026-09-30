#include "motion.hpp"
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <gz/msgs/pose_v.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/sim/System.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/PoseCmd.hh>
#include <gz/transport/Node.hh>

namespace haetae {
class NativePerson final : public gz::sim::System,
    public gz::sim::ISystemConfigure, public gz::sim::ISystemPreUpdate {
 public:
  ~NativePerson() override { node.Unsubscribe("/haetae/native/person_pose"); }
  void Configure(const gz::sim::Entity &, const std::shared_ptr<const sdf::Element> &,
      gz::sim::EntityComponentManager &, gz::sim::EventManager &) override {
    node.Subscribe("/haetae/native/person_pose", &NativePerson::Receive, this);
  }
  void PreUpdate(const gz::sim::UpdateInfo &info,
      gz::sim::EntityComponentManager &ecm) override {
    if (info.paused || info.dt <= std::chrono::steady_clock::duration::zero()) return;
    Body desired;
    std::uint64_t version;
    {
      std::lock_guard<std::mutex> guard(mutex);
      if (!have_target) return;
      desired = target;
      version = target_version;
    }
    if (initialized && applied && settled && version == applied_version) return;
    if (!initialized) { current = desired; initialized = true; }
    else advance(current, desired, std::chrono::duration<double>(info.dt).count());
    settled = current[0].Pos().Distance(desired[0].Pos()) < 1e-12;
    applied_version = version;
    applied = true;
    for (std::size_t i = 0; i < names.size(); ++i) {
      if (entities[i] == gz::sim::kNullEntity)
        entities[i] = ecm.EntityByComponents(gz::sim::components::Name(names[i]),
                                             gz::sim::components::Model());
      if (entities[i] != gz::sim::kNullEntity)
        ecm.SetComponentData<gz::sim::components::WorldPoseCmd>(entities[i], current[i]);
      else applied = false;
    }
  }
 private:
  void Receive(const gz::msgs::Pose_V &msg) {
    if (msg.pose_size() != static_cast<int>(names.size())) return;
    Body next;
    std::array<bool, 12> seen{};
    for (const auto &pose : msg.pose()) {
      const auto found = std::find(names.begin(), names.end(), pose.name());
      if (found == names.end()) return;
      const std::size_t i = static_cast<std::size_t>(found - names.begin());
      if (seen[i]) return;
      const auto &p = pose.position(); const auto &q = pose.orientation();
      for (double v : {p.x(), p.y(), p.z(), q.w(), q.x(), q.y(), q.z()})
        if (!std::isfinite(v)) return;
      const double norm = q.w()*q.w() + q.x()*q.x() + q.y()*q.y() + q.z()*q.z();
      if (std::abs(norm - 1.0) > 1e-6) return;
      next[i] = gz::math::Pose3d(p.x(), p.y(), p.z(), q.w(), q.x(), q.y(), q.z());
      seen[i] = true;
    }
    std::lock_guard<std::mutex> guard(mutex);
    target = next; have_target = true; ++target_version;
  }
  const std::array<std::string, 12> names{{"person_torso", "person_head",
    "person_thigh-1", "person_shin-1", "person_upper_arm-1", "person_forearm-1",
    "person_foot-1", "person_thigh1", "person_shin1", "person_upper_arm1",
    "person_forearm1", "person_foot1"}};
  gz::transport::Node node;
  std::mutex mutex;
  Body target{}, current{};
  std::array<gz::sim::Entity, 12> entities{};
  std::uint64_t target_version{0}, applied_version{0};
  bool have_target{false}, initialized{false}, applied{false}, settled{false};
};
}  // namespace haetae
GZ_ADD_PLUGIN(haetae::NativePerson, gz::sim::System,
              gz::sim::ISystemConfigure, gz::sim::ISystemPreUpdate)
