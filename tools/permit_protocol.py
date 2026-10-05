"""Canonical H2 LED permit encoding shared by authorizer and relay."""
import json
import re
import struct

DOMAIN = b"HAETAE-LED-H2\0\0\0"
FIELDS = {"install", "epoch", "generation", "sequence", "challenge", "issued", "nonce", "op"}


def context(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("invalid permit request fields")
    for name in ("install", "nonce"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[0-9a-f]{32}", value[name]):
            raise ValueError("invalid permit identity/nonce")
    for name in ("epoch", "generation", "sequence", "challenge", "issued"):
        if type(value[name]) is not int or not 0 <= value[name] <= 0xffffffff:
            raise ValueError("invalid permit counter")
    if not value["epoch"] or not value["generation"] or not value["challenge"]:
        raise ValueError("uninitialized controller")
    if value["sequence"] == 0xffffffff or value["op"] not in ("BIND", "ARM", "RUN"):
        raise ValueError("unsupported permit action")
    return value


def payload(value, duration=200):
    context(value)
    if type(duration) is not int or not (duration == 0 and value["op"] == "BIND" or 1 <= duration <= 200 and value["op"] != "BIND"):
        raise ValueError("invalid lease duration")
    return (DOMAIN + bytes.fromhex(value["install"]) + struct.pack("<7I", value["epoch"],
            value["generation"], value["sequence"] + 1, value["challenge"], value["issued"],
            duration, {"ARM": 0, "RUN": 1, "BIND": 2}[value["op"]]) + bytes.fromhex(value["nonce"]))


def bind_payload(value, ephemeral, controller_public):
    if value["op"] != "BIND" or not re.fullmatch(r"[0-9a-f]{64}", ephemeral) or not re.fullmatch(r"[0-9a-f]{64}", controller_public):
        raise ValueError("invalid authenticated key exchange")
    return b"HAETAE-KEX-H2\0\0\0" + payload(value, 0)[16:] + bytes.fromhex(ephemeral) + bytes.fromhex(controller_public)


def frame(value, signature, duration=200, ephemeral=None):
    context(value)
    size = 128 if value["op"] == "BIND" else 64
    if not re.fullmatch(r"[0-9a-f]{" + str(size) + "}", signature):
        raise ValueError("invalid permit signature")
    extra = ""
    if value["op"] == "BIND":
        if duration != 0 or not isinstance(ephemeral, str) or not re.fullmatch(r"[0-9a-f]{64}", ephemeral):
            raise ValueError("invalid BIND frame")
        extra = ephemeral + " "
    elif type(duration) is not int or not 1 <= duration <= 200:
        raise ValueError("invalid permit duration")
    return (f"H2 {value['op']} {value['generation']} {value['sequence'] + 1} "
            f"{value['challenge']} {value['issued']} {duration} {extra}{signature}\n").encode("ascii")


def decode_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON field")
            out[key] = value
        return out
    if len(raw) > 512:
        raise ValueError("oversized permit request")
    return context(json.loads(raw, object_pairs_hook=pairs))
