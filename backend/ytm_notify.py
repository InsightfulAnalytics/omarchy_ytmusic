"""Desktop notifications, sent from the worker over D-Bus.

- ``notify({title, body?, timeoutMs?, execArgv?})`` shows one notification
  through org.freedesktop.Notifications (the Omarchy shell's own server), as
  omarchy-notification-send does: app name YouTube Music, low urgency, and
  ``execArgv`` (a program and its arguments) as the omarchy-exec-argv hint
  that a click runs. ``timeoutMs`` defaults to the server's own. Returns
  ``{id}``.

Why the worker and not omarchy-notification-send: that helper takes the
text as command-line arguments, and a process's command line is readable by
every local account (/proc/<pid>/cmdline). Titles here are song, album and
playlist names, some of them private. The worker reads them from its stdin
and hands them to the session bus, which only this user can reach.

Area module (see AREA_MODULES in ytm.py). The text never reaches a log.
"""

import json

from ytm_proto import WorkerError

APP_NAME = "YouTube Music"
URGENCY_LOW = 0
TEXT_MAX = 2000
DBUS_TIMEOUT_S = 3


def _open():
    from jeepney.io.blocking import open_dbus_connection

    return open_dbus_connection(bus="SESSION")


def _text(params, key, required):
    value = params.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value):
        raise WorkerError("BAD_REQUEST", f"'{key}' must be a non-empty string" if required else f"'{key}' must be a string")
    return value[:TEXT_MAX]


def message(params):
    """The Notify call for these params (a jeepney Message)."""
    from jeepney import DBusAddress, new_method_call

    title = _text(params, "title", True)
    body = _text(params, "body", False)
    timeout = params.get("timeoutMs", -1)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < -1:
        raise WorkerError("BAD_REQUEST", "'timeoutMs' must be an integer of -1 or more")
    hints = {"urgency": ("y", URGENCY_LOW)}
    argv = params.get("execArgv")
    if argv is not None:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or not argv[0]:
            raise WorkerError("BAD_REQUEST", "'execArgv' must be a non-empty list of strings")
        hints["omarchy-exec-argv"] = ("s", json.dumps(argv))
    address = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                          interface="org.freedesktop.Notifications")
    return new_method_call(address, "Notify", "susssasa{sv}i",
                           (APP_NAME, 0, "", title, body, [], hints, timeout))


def m_notify(worker, params):
    msg = message(params)
    try:
        from jeepney.wrappers import unwrap_msg

        with _open() as conn:
            reply = conn.send_and_get_reply(msg, timeout=DBUS_TIMEOUT_S)
        (notification_id,) = unwrap_msg(reply)
    except Exception as exc:  # noqa: BLE001 - no bus, no server, a refusal: all one failure
        raise WorkerError("INTERNAL", f"Could not show the notification ({type(exc).__name__})") from None
    return {"id": notification_id}


METHODS = {"notify": m_notify}
CACHEABLE = set()
# Its own lane: a notification never waits behind a slow read.
LANES = {"notify": "notify"}
INVALIDATES = {}
RECORDERS = {}
MUTATES = set()
