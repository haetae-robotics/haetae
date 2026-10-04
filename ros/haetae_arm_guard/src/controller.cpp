// Reference-only position controller. This is not a hardware safety function.
#include "haetae_arm_guard/lease.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <joint_trajectory_controller/joint_trajectory_controller.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <rclcpp_action/create_server.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/u_int64.hpp>

namespace haetae_arm_guard
{
class LeaseTrajectoryController : public joint_trajectory_controller::JointTrajectoryController
{
  using Base = joint_trajectory_controller::JointTrajectoryController;
  using Return = controller_interface::CallbackReturn;
  using Trajectory = trajectory_msgs::msg::JointTrajectory;
  realtime_tools::RealtimeBuffer<Lease> lease_;
  std::atomic<uint64_t> last_stamp_{0};
  std::atomic<int64_t> cutoff_ms_{0}, updated_ms_{0};
  std::atomic<int64_t> stop_wall_ns_{0};
  std::atomic<bool> holding_{true};
  bool was_holding_ = true;
  std::vector<double> guard_hold_;
  rclcpp::Subscription<std_msgs::msg::UInt64>::SharedPtr heartbeat_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr guard_state_;
  rclcpp::TimerBase::SharedPtr state_timer_;

  static int64_t wall_ns()
  {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  }

  bool alive_nonrt()
  {
    return fresh(*lease_.readFromNonRT(), get_node()->now().nanoseconds() / 1000000, wall_ns());
  }

  bool current_goal(const Trajectory & trajectory)
  {
    const auto stamp = rclcpp::Time(trajectory.header.stamp).nanoseconds() / 1000000;
    const auto now = get_node()->now().nanoseconds() / 1000000;
    return stamp > 0 && stamp >= cutoff_ms_.load() && stamp <= now + 20 &&
           now - stamp <= 50 && !holding_.load() && alive_nonrt();
  }

public:
  Return on_configure(const rclcpp_lifecycle::State & state) override
  {
    auto result = Base::on_configure(state);
    if (result != Return::SUCCESS) {return result;}
    if (params_.command_interfaces != std::vector<std::string>{"position"} ||
      params_.state_interfaces != std::vector<std::string>{"position", "velocity"} ||
      params_.allow_partial_joints_goal)
    {
      RCLCPP_ERROR(get_node()->get_logger(), "Guard requires full position/velocity feedback and position commands");
      return Return::ERROR;
    }
    heartbeat_ = get_node()->create_subscription<std_msgs::msg::UInt64>(
      "/haetae_gate/heartbeat", rclcpp::QoS(1).best_effort(),
      [this](std_msgs::msg::UInt64::ConstSharedPtr msg) {
        if (!admissible(msg->data, last_stamp_.load(), get_node()->now().nanoseconds() / 1000000)) {return;}
        last_stamp_.store(msg->data);
        lease_.writeFromNonRT(Lease{msg->data, wall_ns()});
      });
    // Replace both inherited ingress paths; stale/closed goals never enter the
    // base controller. Recheck acceptance after the asynchronous handshake.
    action_server_.reset();
    action_server_ = rclcpp_action::create_server<FollowJTrajAction>(get_node(),
      "~/follow_joint_trajectory",
      [this](const rclcpp_action::GoalUUID & id, std::shared_ptr<const FollowJTrajAction::Goal> goal) {
        return current_goal(goal->trajectory) ? Base::goal_received_callback(id, goal) :
               rclcpp_action::GoalResponse::REJECT;
      },
      [this](std::shared_ptr<rclcpp_action::ServerGoalHandle<FollowJTrajAction>> goal) {
        return Base::goal_cancelled_callback(goal);
      },
      [this](std::shared_ptr<rclcpp_action::ServerGoalHandle<FollowJTrajAction>> goal) {
        if (!current_goal(goal->get_goal()->trajectory)) {
          auto result_msg = std::make_shared<FollowJTrajAction::Result>();
          result_msg->error_code = FollowJTrajAction::Result::INVALID_GOAL;
          result_msg->error_string = "Gateway lease expired before goal acceptance";
          goal->abort(result_msg);
          return;
        }
        Base::goal_accepted_callback(goal);
      });
    joint_command_subscriber_.reset();
    joint_command_subscriber_ = get_node()->create_subscription<Trajectory>(
      "~/joint_trajectory", rclcpp::QoS(1), [this](Trajectory::SharedPtr msg) {
        if (current_goal(*msg)) {Base::topic_callback(msg);}
      });
    guard_state_ = get_node()->create_publisher<std_msgs::msg::String>(
      "~/guard_state", rclcpp::QoS(1).transient_local());
    state_timer_ = get_node()->create_wall_timer(std::chrono::milliseconds(50), [this]() {
        const auto lease = *lease_.readFromNonRT();
        std_msgs::msg::String msg;
        msg.data = std::string("{\"holding\":") + (holding_.load() ? "true" : "false") +
          ",\"stamp_ms\":" + std::to_string(updated_ms_.load()) +
          ",\"cutoff_ms\":" + std::to_string(cutoff_ms_.load()) + "}";
        msg.data.pop_back();
        msg.data += ",\"stop_wall_ns\":" + std::to_string(stop_wall_ns_.load()) +
          ",\"lease_sent_ms\":" + std::to_string(lease.sent_ms) +
          ",\"lease_received_wall_ns\":" + std::to_string(lease.received_ns) + "}";
        guard_state_->publish(msg);
      });
    return Return::SUCCESS;
  }

  Return on_activate(const rclcpp_lifecycle::State & state) override
  {
    auto result = Base::on_activate(state);
    if (result == Return::SUCCESS) {
      lease_.initRT(Lease{});
      last_stamp_.store(0);
      cutoff_ms_.store(get_node()->now().nanoseconds() / 1000000);
      holding_.store(true);
      was_holding_ = true;
      guard_hold_ = state_current_.positions;
    }
    return result;
  }

  controller_interface::return_type update(const rclcpp::Time & time,
    const rclcpp::Duration & period) override
  {
    const auto stamp = time.nanoseconds() / 1000000;
    updated_ms_.store(stamp);
    read_state_from_state_interfaces(state_current_);
    const bool feedback_ok = std::all_of(state_current_.positions.begin(),
      state_current_.positions.end(), [](double value) {return std::isfinite(value);});
    const bool allowed = feedback_ok && fresh(*lease_.readFromRT(), stamp, wall_ns());
    if (allowed) {
      if (was_holding_) {
        // Ingress remains closed until recovery has discarded queued work.
        new_trajectory_msg_.writeFromNonRT(set_hold_position());
      }
      was_holding_ = false;
      guard_hold_ = state_current_.positions;
      holding_.store(false);
      return Base::update(time, period);
    }
    holding_.store(true);
    if (!was_holding_) {
      cutoff_ms_.store(stamp);
      stop_wall_ns_.store(wall_ns());
      if (feedback_ok) {guard_hold_ = state_current_.positions;}
    }
    was_holding_ = true;
    const auto goal = *rt_active_goal_.readFromRT();
    if (goal) {
      auto result = std::make_shared<FollowJTrajAction::Result>();
      result->error_code = FollowJTrajAction::Result::PATH_TOLERANCE_VIOLATED;
      result->error_string = "Independent controller stop: gateway lease expired";
      goal->setAborted(result);
      rt_active_goal_.writeFromNonRT(RealtimeGoalHandlePtr());
    }
    rt_has_pending_goal_ = false;
    // Remove the old trajectory, not just its output. A later heartbeat must
    // not revive queued motion. No base update executes while the lease is lost.
    state_current_.positions = guard_hold_;
    auto hold = set_hold_position();
    new_trajectory_msg_.writeFromNonRT(hold);
    current_trajectory_->update(hold);
    for (size_t i = 0; i < num_cmd_joints_; ++i) {
      if (!joint_command_interface_[0][i].get().set_value(guard_hold_[map_cmd_to_joints_[i]])) {
        return controller_interface::return_type::ERROR;
      }
    }
    last_commanded_state_ = state_current_;
    last_commanded_time_ = time;
    return controller_interface::return_type::OK;
  }
};
}  // namespace haetae_arm_guard
PLUGINLIB_EXPORT_CLASS(haetae_arm_guard::LeaseTrajectoryController, controller_interface::ControllerInterface)
