"""Install an inherited network syscall filter before executing a role.

Only IPv4 UDP and unprivileged route-interface queries may open sockets.
UNIX socketpairs are allowed for internal IPC; external UNIX connections,
TCP, packet sockets and io_uring are denied. No inherited network descriptors
are passed by the root launcher. libseccomp supports the native architecture.
"""
import os
from pathlib import Path
import socket
import sys


def install():
    import errno
    import seccomp
    if os.geteuid() == 0:
        raise RuntimeError("role sandbox must be launched after dropping UID")
    state = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                 if ":" in line)
    if any(int(state[name].strip(), 16) for name in ("CapEff", "CapPrm", "CapAmb")):
        raise RuntimeError("role retains Linux capabilities")
    guard = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    deny = seccomp.ERRNO(errno.EPERM)
    # Default filter action is ALLOW, so reject every other socket family,
    # including future families. Keep AF_NETLINK/NETLINK_ROUTE for Fast DDS.
    for family in range(socket.AF_NETLINK + 1):
        if family not in (socket.AF_INET, socket.AF_NETLINK):
            guard.add_rule(deny, "socket", seccomp.Arg(0, seccomp.EQ, family))
    guard.add_rule(deny, "socket", seccomp.Arg(0, seccomp.GT, socket.AF_NETLINK))
    for kind in range(16):
        if kind != socket.SOCK_DGRAM:
            guard.add_rule(deny, "socket", seccomp.Arg(0, seccomp.EQ, socket.AF_INET),
                           seccomp.Arg(1, seccomp.MASKED_EQ, 15, kind))
    guard.add_rule(deny, "socket", seccomp.Arg(0, seccomp.EQ, socket.AF_NETLINK),
                   seccomp.Arg(2, seccomp.NE, 0))
    guard.add_rule(deny, "socketpair", seccomp.Arg(0, seccomp.NE, socket.AF_UNIX))
    for syscall in ("io_uring_setup", "ptrace", "process_vm_writev", "pidfd_getfd"):
        guard.add_rule(deny, syscall)
    guard.set_attr(seccomp.Attr.CTL_NNP, 1)
    guard.load()
    if "NoNewPrivs:\t1" not in Path("/proc/self/status").read_text():
        raise RuntimeError("no_new_privs was not installed")


if __name__ == "__main__":
    install()
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
