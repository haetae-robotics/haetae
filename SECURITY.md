# Security policy

## Supported scope

Haetae is pre-alpha. No version is supported as a certified safety function or
as a protective deployment for a physical robot. We review vulnerability
reports for the current `main` branch and the latest published package. See
[the security release gate](docs/security-release.md) for the tested and open
attack paths.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/haetae-robotics/haetae/security/advisories/new)
to send a report to the maintainers. Include the affected commit or version,
the trust boundary or robot interface involved, steps to reproduce, and the
impact on command blocking, logs or state. Keep exploit details and any key
material out of public issues while the report is being evaluated.

If a robot may be moving unsafely, use its independent stop mechanism first.
Do not rely on a Haetae process or ROS topic as an emergency stop.
