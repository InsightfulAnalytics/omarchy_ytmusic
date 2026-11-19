"""ytm_notify: notifications go over D-Bus from the worker, never through a
command line. Pinned against a fake connection: the real one shows a
notification on the desktop."""

import html
import re
import json

import pytest
from jeepney.low_level import Message

import ytm
import ytm_notify as notify
from ytm_proto import WorkerError


class FakeConn:
    def __init__(self, reply=None, error=None):
        self.sent = []
        self.reply = reply
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_and_get_reply(self, msg, timeout=None):
        self.sent.append((msg, timeout))
        if self.error:
            raise self.error
        return self.reply(msg)


def method_return(msg, value):
    from jeepney import new_method_return

    return new_method_return(msg, "u", (value,))


def test_message_is_a_notify_call_with_the_text_as_parameters():
    msg = notify.message({"title": "Added to My secret mix", "body": "-rf --hint=x", "timeoutMs": 2500})
    assert isinstance(msg, Message)
    assert msg.header.fields[3] == "Notify"  # HeaderFields.member
    app, replaces, icon, title, body, actions, hints, timeout = msg.body
    assert (app, replaces, icon, actions, timeout) == ("YouTube Music", 0, "", ["default", ""], 2500)
    assert (title, body) == ("Added to My secret mix", "-rf --hint=x")
    assert hints == {"urgency": ("y", 0)}


@pytest.mark.parametrize("text,shown", [
    ("Tom & Jerry", "Tom &amp; Jerry"),
    ('&lt;a href="https://x/"&gt;Session expired&lt;/a&gt;',
     '\uff06lt;a href="https:\u2060//x/"\uff06gt;Session expired\uff06lt;/a\uff06gt;'),
    ("<a href=https://x>y</a>", "\u2039a href=https:\u2060//x\u203ay\u2039/a\u203a"),
    ("Free download https://evil.example/x", "Free download https:\u2060//evil.example/x"),
    ("HTTP://A.example and ftp://b", "HTTP:\u2060//A.example and ftp:\u2060//b"),
    ("Live: 10:30 / ratio 2:1", "Live: 10:30 / ratio 2:1"),
    ("a &amp;lt; b", "a \uff06amp;lt; b"),
])
def test_remote_text_never_becomes_markup(text, shown):
    """A title from YouTube in a notification: the server decodes entities
    more than once and turns <a href> into a link."""
    title, body = notify.message({"title": text, "body": text}).body[3:5]
    assert title == shown and body == shown
    decoded = body
    for _ in range(3):
        decoded = html.unescape(decoded)
    assert "<" not in decoded and ">" not in decoded, "no tag after any number of decodes"
    # Omarchy's notification server links any match of this (Markup.js, Detect.js).
    assert not re.search(r"https?://", decoded, re.I), "no URL to link"


def test_exec_argv_becomes_the_click_hint():
    msg = notify.message({"title": "Sign in", "execArgv": ["omarchy-launch-x", "bin/ytm auth"]})
    hints = msg.body[6]
    assert json.loads(hints["omarchy-exec-argv"][1]) == ["omarchy-launch-x", "bin/ytm auth"]
    assert msg.body[7] == -1  # the server's own timeout


@pytest.mark.parametrize("params", [
    {},
    {"title": ""},
    {"title": 3},
    {"title": "t", "body": 4},
    {"title": "t", "timeoutMs": "5"},
    {"title": "t", "timeoutMs": -2},
    {"title": "t", "timeoutMs": True},
    {"title": "t", "execArgv": []},
    {"title": "t", "execArgv": "sh -c x"},
    {"title": "t", "execArgv": ["", "x"]},
])
def test_bad_params_are_refused(params):
    with pytest.raises(WorkerError) as err:
        notify.message(params)
    assert err.value.code == "BAD_REQUEST"


def test_long_text_is_cut():
    msg = notify.message({"title": "x" * 5000})
    assert len(msg.body[3]) == notify.TEXT_MAX


def test_notify_returns_the_id(monkeypatch):
    conn = FakeConn(reply=lambda msg: method_return(msg, 42))
    monkeypatch.setattr(notify, "_open", lambda: conn)
    assert notify.m_notify(None, {"title": "Playing next", "body": "Album"}) == {"id": 42}
    assert conn.sent[0][1] == notify.DBUS_TIMEOUT_S


def test_a_bus_failure_is_internal_and_names_no_text(monkeypatch):
    monkeypatch.setattr(notify, "_open", lambda: FakeConn(error=OSError("no bus for My secret mix")))
    with pytest.raises(WorkerError) as err:
        notify.m_notify(None, {"title": "My secret mix"})
    assert err.value.code == "INTERNAL"
    assert "secret" not in err.value.message


def test_notify_runs_on_its_own_lane():
    assert ytm.lane_of({"method": "notify", "params": {"title": "t"}}) == "notify"
    assert "notify" not in ytm.MUTATES
