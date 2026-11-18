# P0 probe for K2: AppArmor deny rules *inside* an allowed tree, which
# Landlock cannot express, plus signal mediation (the agent must not be able
# to kill the daemon). Loaded only for the duration of the check.
abi <abi/4.0>,
include <tunables/global>

profile rootsh-p0-probe flags=(attach_disconnected) {
  include <abstractions/base>

  /{,usr/}bin/** rix,
  # Ubuntu 26.04 coreutils are uutils: /usr/bin/touch resolves here, and
  # AppArmor mediates the resolved path.
  /usr/lib/cargo/bin/** rix,

  /tmp/rootsh-p0-apparmor/ r,
  /tmp/rootsh-p0-apparmor/** rw,
  deny /tmp/rootsh-p0-apparmor/protected/** w,
}
