"""Desktop ask-channel probe: org.freedesktop.Notifications over D-Bus.

libnotify's `notify-send -A` refuses actions on this host even though the
GNOME notification service advertises the `actions` capability, so this
probe calls Notify directly and waits for ActionInvoked or NotificationClosed.
No answer within the timeout closes the notification and counts as no
decision (Rootsh treats that as block).

Usage: python3 -I dbus_notify_probe.py <timeout_seconds>
"""

from __future__ import annotations

import json
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
CLOSE_REASONS = {1: "expired", 2: "dismissed", 3: "closed_by_call", 4: "undefined"}
URGENCY_CRITICAL = 2


def ask(summary: str, body: str, actions: dict[str, str], timeout_s: int) -> dict[str, object]:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    loop = GLib.MainLoop()
    state: dict[str, object] = {"id": None, "action": None, "closed": None, "signals": []}
    started = time.monotonic()

    def on_signal(_conn, _sender, _path, _iface, signal, params, _data) -> None:
        notification_id = params.unpack()[0]
        if notification_id != state["id"]:
            return
        state["signals"].append(signal)
        # Spec 1.2 servers emit ActivationToken just before ActionInvoked;
        # only the action or the close is a decision.
        if signal == "ActionInvoked":
            state["action"] = params.unpack()[1]
            loop.quit()
        elif signal == "NotificationClosed":
            state["closed"] = CLOSE_REASONS.get(params.unpack()[1], "unknown")
            loop.quit()

    # Subscribe before sending so a fast click cannot be missed.
    subscription = bus.signal_subscribe(BUS_NAME, BUS_NAME, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, on_signal, None)
    flat_actions = [item for key, label in actions.items() for item in (key, label)]
    reply = bus.call_sync(
        BUS_NAME, OBJECT_PATH, BUS_NAME, "Notify",
        GLib.Variant("(susssasa{sv}i)", ("Rootsh", 0, "", summary, body, flat_actions,
                                         {"urgency": GLib.Variant("y", URGENCY_CRITICAL)}, 0)),
        GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None,
    )
    state["id"] = reply.unpack()[0]

    def on_timeout() -> bool:
        bus.call_sync(BUS_NAME, OBJECT_PATH, BUS_NAME, "CloseNotification", GLib.Variant("(u)", (state["id"],)),
                      None, Gio.DBusCallFlags.NONE, -1, None)
        state["closed"] = state["closed"] or "timeout"
        loop.quit()
        return False

    timer = GLib.timeout_add_seconds(timeout_s, on_timeout)
    loop.run()
    if state["closed"] != "timeout":  # the timer source is still live
        GLib.source_remove(timer)
    bus.signal_unsubscribe(subscription)
    return {**state, "elapsed_s": round(time.monotonic() - started, 1)}


def main(timeout_s: int) -> int:
    result = ask(
        "Rootsh P0 check",
        "You asked: nothing.\nThe check is doing: this test.\nClick Allow (or let it time out).",
        {"block": "Block", "allow": "Allow"},
        timeout_s,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
