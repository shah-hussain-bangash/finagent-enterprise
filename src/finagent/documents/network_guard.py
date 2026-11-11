"""Linux parser-only seccomp restrictions; no Python socket monkeypatching.

Install before reading document bytes or importing third-party parsers. The parent must start a
fresh isolated interpreter with closed inherited handles. Not a complete filesystem sandbox.
"""

from __future__ import annotations

import ctypes
import errno
import sys

# Block network creation/use, io_uring bypasses and process creation/replacement. libseccomp
# includes an architecture check and handles architecture-specific syscall numbers.
DENIED_SYSCALLS = (
    "socket",
    "socketpair",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "sendto",
    "recvfrom",
    "sendmsg",
    "recvmsg",
    "sendmmsg",
    "recvmmsg",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "clone",
    "clone3",
    "fork",
    "vfork",
    "execve",
    "execveat",
    "ptrace",
    "process_vm_writev",
    "pidfd_getfd",
    "bpf",
    "setns",
    "unshare",
)


def apply_network_limit() -> bool:
    if sys.platform != "linux":
        return False
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        # Variadic API: only the fixed parameters are used (zero argument comparisons).
        lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
        lib.seccomp_rule_add.restype = ctypes.c_int
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_load.restype = ctypes.c_int
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        lib.seccomp_release.restype = None
        lib.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        lib.seccomp_attr_set.restype = ctypes.c_int
        context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
        if not context:
            return False
        try:
            # SCMP_FLTATR_CTL_NNP: fail if no_new_privs cannot be enabled on filter load.
            if lib.seccomp_attr_set(context, 3, 1) != 0:
                return False
            for name in DENIED_SYSCALLS:
                number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if number == -1:
                    return False
                if lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                    return False
            return bool(lib.seccomp_load(context) == 0)
        finally:
            lib.seccomp_release(context)
    except (OSError, AttributeError, ValueError):
        return False


def verify_network_limit() -> bool:
    """Probe native libc, including UDP/IPv6/Unix sockets, without sending network traffic."""
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        libc.socket.restype = ctypes.c_int
        libc.close.argtypes = [ctypes.c_int]
        libc.close.restype = ctypes.c_int
        for family, kind in ((2, 1), (2, 2), (10, 1), (10, 2), (1, 1)):
            ctypes.set_errno(0)
            descriptor = libc.socket(family, kind, 0)
            if descriptor >= 0:
                libc.close(descriptor)
                return False
            if ctypes.get_errno() != errno.EPERM:
                return False
        return True
    except (OSError, AttributeError):
        return False
