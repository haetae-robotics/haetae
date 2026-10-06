"""Distinguish measured fail-closed scheduling loss from normal availability."""
import json


def scheduling_loss(log, code, scenario, events):
    if code != 1 or scenario != "allow" or "device lease locked; explicit new run required" not in log:
        return None
    markers = []
    for line in log.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event") == "device_lease_expired":
            markers.append(value)
    if len(markers) != 1:
        return None
    marker = markers[0]
    gap = marker.get("host_gap_ms")
    if (marker.get("status") != "LOCKED" or marker.get("automatic_rearm") is not False
            or type(gap) not in (int, float) or not 200 <= gap < float("inf")):
        return None
    for index, stop in enumerate(events):
        prior = [event for event in events[:index] if event.get("on")]
        if not prior or stop.get("on") or stop.get("reason") != "lease":
            continue
        delay = stop["device_ms"] - prior[-1]["renewed_ms"]
        if 200 <= delay <= 300 and not any(event.get("on") for event in events[index + 1:]):
            return stop
    return None
