// Exact signed twist admission and independent dual-clock wheel stop.
#include "haetae_arm_guard/payload.hpp"
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"
#include <diff_drive_controller/diff_drive_controller.hpp>
#pragma GCC diagnostic pop
#include <pluginlib/class_list_macros.hpp>
#include <std_msgs/msg/string.hpp>

namespace haetae_arm_guard
{
class PermitDiffDriveController : public diff_drive_controller::DiffDriveController
{
  using Base = diff_drive_controller::DiffDriveController;
  using Return = controller_interface::CallbackReturn;
  PermitGuard permit_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_;
  rclcpp::TimerBase::SharedPtr timer_;
public:
  Return on_configure(const rclcpp_lifecycle::State & state) override
  {
    auto result = Base::on_configure(state);
    if (result != Return::SUCCESS) {return result;}
    get_node()->declare_parameter<std::string>("permit_public_key", "");
    try {permit_.configure(get_node()->get_parameter("permit_public_key").as_string(), "base");}
    catch (const std::exception & e) {RCLCPP_ERROR(get_node()->get_logger(), "%s", e.what()); return Return::ERROR;}
    velocity_command_subscriber_.reset();
    velocity_command_subscriber_ = get_node()->create_subscription<geometry_msgs::msg::TwistStamped>(
      "~/cmd_vel", rclcpp::QoS(1).best_effort(), [this](geometry_msgs::msg::TwistStamped::ConstSharedPtr msg) {
        try {
          const bool zero = msg->twist.linear.x == 0 && msg->twist.angular.z == 0;
          const bool reset = msg->header.frame_id.rfind("v1:base-reset:", 0) == 0;
          const bool stop = msg->header.frame_id.rfind("v1:base-stop:", 0) == 0;
          if ((reset || stop) && !zero) {permit_.reject(); return;}
          const auto kind = reset ? "reset" : stop ? "stop" : "command";
          if (subscriber_is_active_ && permit_.accept(msg->header.frame_id, kind, base_digest(*msg),
            get_node()->now().nanoseconds(), steady_ns(), reset, stop)) {
            received_velocity_msg_.set(*msg);
          } else {
            RCLCPP_WARN_THROTTLE(get_node()->get_logger(), *get_node()->get_clock(), 1000,
              "Base %s rejected: %s %s", kind, permit_.reason().c_str(), permit_.diagnostic().c_str());
          }
        } catch (const std::exception &) {permit_.reject();}
      });
    state_ = get_node()->create_publisher<std_msgs::msg::String>("~/guard_state", rclcpp::QoS(1).transient_local());
    timer_ = get_node()->create_wall_timer(std::chrono::milliseconds(20), [this]() {
      std_msgs::msg::String msg;
      msg.data = "{\"nonce\":\"" + permit_.nonce() + "\",\"holding\":" +
        (permit_.fresh(get_node()->now().nanoseconds(), steady_ns()) ? "false" : "true") +
        ",\"accepted\":" + std::to_string(permit_.accepted()) +
        ",\"published_wall_ns\":" + std::to_string(steady_ns()) +
        ",\"rejected\":" + std::to_string(permit_.rejected()) +
        ",\"reason\":\"" + permit_.reason() + "\"}";
      state_->publish(msg);
    });
    return result;
  }
  Return on_activate(const rclcpp_lifecycle::State & state) override
  {
    auto result = Base::on_activate(state);
    if (result == Return::SUCCESS) {permit_.activate();}
    return result;
  }
  controller_interface::return_type update_and_write_commands(const rclcpp::Time & time,
    const rclcpp::Duration & period) override
  {
    bool allowed = false;
    try {allowed = permit_.authorizes(time.nanoseconds(), steady_ns(), base_digest(command_msg_));}
    catch (const std::exception &) {allowed = false;}
    if (allowed) {
      // Exported chain references cannot change the verified command.
      if (!ordered_exported_reference_interfaces_[0]->set_value(command_msg_.twist.linear.x) ||
        !ordered_exported_reference_interfaces_[1]->set_value(command_msg_.twist.angular.z)) {
        permit_.stop(); halt(); return controller_interface::return_type::ERROR;
      }
    }
    if (!allowed) {
      if (!ordered_exported_reference_interfaces_[0]->set_value(0.0) ||
        !ordered_exported_reference_interfaces_[1]->set_value(0.0)) {
        halt(); return controller_interface::return_type::ERROR;
      }
      received_velocity_msg_.set(geometry_msgs::msg::TwistStamped{});
      command_msg_ = geometry_msgs::msg::TwistStamped{};
      while (!previous_two_commands_.empty()) {previous_two_commands_.pop();}
      previous_two_commands_.push({0, 0}); previous_two_commands_.push({0, 0});
    }
    const auto result = Base::update_and_write_commands(time, period);
    // Bypass acceleration smoothing for the loss-of-authority zero output.
    if (!allowed) {halt();}
    return result;
  }
protected:
  bool on_set_chained_mode(bool chained) override {return !chained;}
};
}  // namespace haetae_arm_guard
PLUGINLIB_EXPORT_CLASS(haetae_arm_guard::PermitDiffDriveController, controller_interface::ChainableControllerInterface)
