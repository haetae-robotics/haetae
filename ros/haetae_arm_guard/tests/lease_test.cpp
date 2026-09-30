#include "haetae_arm_guard/lease.hpp"
#include <cstdlib>
#include <iostream>

void check(bool value, const char * name)
{
  if (!value) {std::cerr << name << '\n'; std::exit(1);}
}

int main()
{
  using haetae_arm_guard::admissible;
  using haetae_arm_guard::fresh;
  check(!admissible(0, 0, 1000), "zero timestamp");
  check(admissible(1000, 999, 1000), "fresh renewal");
  check(!admissible(1000, 1000, 1000), "duplicate does not renew");
  check(!admissible(999, 1000, 1000), "replay does not renew");
  check(!admissible(949, 0, 1000), "delayed renewal");
  check(!admissible(1021, 0, 1000), "far future renewal");
  check(!admissible(1000, 0, -1), "negative clock");
  const haetae_arm_guard::Lease lease{1000, 1000000000};
  check(fresh(lease, 1000, 1000000000), "initial fresh lease");
  check(!fresh({}, 1000, 1000000000), "no lease");
  check(!fresh(lease, 999, 1000000000), "backwards simulator clock");
  check(!fresh(lease, 1000, 999999999), "backwards wall clock");
  check(!fresh(lease, 1250, 1000000001), "sim deadline");
  check(!fresh(lease, 1001, 1250000000), "wall deadline even with paused sim");
  check(fresh(lease, 1249, 1249000000), "both clocks within deadline");
  std::cout << "lease policy passed\n";
}
