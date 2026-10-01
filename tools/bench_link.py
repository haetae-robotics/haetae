"""Bounded USB serial link. Its session/challenge tokens are NOT authentication."""
import errno
import fcntl
import os
import secrets
import select
import struct
import termios
import time


class LinkError(RuntimeError):
    pass


class SerialLink:
    def __init__(self, port):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
            try:
                fcntl.ioctl(self.fd, termios.TIOCMBIS,
                            struct.pack("I", termios.TIOCM_DTR | termios.TIOCM_RTS))
            except OSError as exc:
                if exc.errno != errno.ENOTTY:  # PTY emulator has no modem lines.
                    raise
        except BaseException:
            os.close(self.fd)
            raise
        self.session = secrets.token_hex(8)
        self.seq = self.challenge = 0
        self.buffer = bytearray()

    def exchange(self, op, timeout=0.050):
        seq = 0 if op == "HELLO" else self.seq + 1
        token = 0 if op == "HELLO" else self.challenge
        raw = f"H1 {op} {self.session} {seq} {token}\n".encode()
        deadline = time.monotonic() + timeout
        while raw:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [self.fd], [], left)[1]:
                raise LinkError("USB write timeout")
            count = os.write(self.fd, raw)
            if not count:
                raise LinkError("USB closed")
            raw = raw[count:]
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise LinkError("USB response timeout")
            data = os.read(self.fd, 128)
            if not data:
                raise LinkError("USB closed")
            self.buffer.extend(data)
            if len(self.buffer) > 128:
                raise LinkError("oversized USB response")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        try:
            fields = line.decode("ascii").split(" ")
            expected = 1 if op == "HELLO" else token + 1
            if (len(fields) != 7 or fields[:3] != ["H1", "OK", self.session]
                    or fields[3] != str(seq) or fields[4] != str(expected)
                    or fields[5] not in ("ON", "ARMED", "LOCKED") or rest):
                raise ValueError("unexpected USB response: " + line.decode("ascii"))
            required = {"HELLO": "LOCKED", "ARM": "ARMED", "RUN": "ON", "STOP": "LOCKED"}
            if op in required and fields[5] != required[op]:
                raise ValueError("incorrect device state")
        except (ValueError, UnicodeError) as exc:
            raise LinkError(str(exc)) from exc
        self.seq, self.challenge = seq, expected
        return fields[5]

    def stop(self):
        try:
            self.exchange("STOP")
        except (LinkError, OSError):
            pass  # A failed link relies on the independent device lease.

    def close(self):
        try:
            fcntl.ioctl(self.fd, termios.TIOCNXCL)
        except OSError:
            pass
        finally:
            os.close(self.fd)
