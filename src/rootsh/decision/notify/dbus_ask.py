"""D-Bus pop-up helper, run as `/usr/bin/python3 -I dbus_ask.py` (needs `gi`).

Reads {"summary", "body", "timeout"} on stdin; shows a critical notification
with Block/Allow; prints one JSON line {"action", "closed"} and exits. It
imports nothing from rootsh: the system Python runs it in isolated mode.
Spec-1.2 servers send ActivationToken before ActionInvoked, so only
ActionInvoked or NotificationClosed ends the wait (P0 §5).
"""

from __future__ import annotations

import json
import sys
from typing import Any

BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
URGENCY_CRITICAL = 2


def show(summary: str, body: str, timeout_s: int) -> dict[str, object]:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    loop = GLib.MainLoop()
    state: dict[str, Any] = {"id": None, "action": None, "closed": None}

    def on_signal(*args: Any) -> None:
        signal, params = args[4], args[5].unpack()
        if params[0] != state["id"]:
            return
        if signal == "ActionInvoked":
            state["action"] = params[1]
            loop.quit()
        elif signal == "NotificationClosed":
            state["closed"] = "closed"
            loop.quit()

    # Subscribe before sending so a fast click cannot be missed.
    sub = bus.signal_subscribe(
        BUS_NAME, BUS_NAME, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, on_signal, None
    )
    hints = {"urgency": GLib.Variant("y", URGENCY_CRITICAL)}
    args = ("Rootsh", 0, "", summary, body, ["block", "Block", "allow", "Allow"], hints, 0)
    reply = bus.call_sync(
        BUS_NAME, OBJECT_PATH, BUS_NAME, "Notify", GLib.Variant("(susssasa{sv}i)", args),
        GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None,
    )  # fmt: skip
    state["id"] = reply.unpack()[0]

    def on_timeout() -> bool:
        state["closed"] = "timeout"
        loop.quit()
        return False

    timer = GLib.timeout_add_seconds(timeout_s, on_timeout)
    loop.run()
    if state["closed"] == "timeout":
        bus.call_sync(
            BUS_NAME, OBJECT_PATH, BUS_NAME, "CloseNotification",
            GLib.Variant("(u)", (state["id"],)), None, Gio.DBusCallFlags.NONE, -1, None,
        )  # fmt: skip
    else:
        GLib.source_remove(timer)
    bus.signal_unsubscribe(sub)
    return {"action": state["action"], "closed": state["closed"]}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        result = show(str(request["summary"]), str(request["body"]), int(request["timeout"]))
    except Exception as exc:  # noqa: BLE001 - reported as no answer, which blocks
        result = {"action": None, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
