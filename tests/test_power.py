"""Sleep inhibits: refcount, Windows execution state, and Linux fallbacks."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from voxprint_common import power
from voxprint_common.suite_settings import (
    file,
    keep_awake_during_jobs,
    set_keep_awake_during_jobs,
    values,
)

_REAL_START = power._start_inhibit

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class _Count:
    """Inhibit stand-in that records close."""

    def __init__(self) -> None:
        self.closed = False
        self.displays: list[bool] = []

    def update(self, *, display: bool) -> None:
        self.displays.append(display)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _block_real_inhibit(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid(reason: str, display: bool) -> _Count:
        raise AssertionError(f"real inhibit for {reason!r} display={display}")

    monkeypatch.setattr(power, "_start_inhibit", forbid)
    power._reset_for_tests()
    yield
    power._reset_for_tests()


def _install_counter(monkeypatch: pytest.MonkeyPatch) -> list[_Count]:
    created: list[_Count] = []

    def start(reason: str, display: bool) -> _Count:
        hold = _Count()
        created.append(hold)
        return hold

    monkeypatch.setattr(power, "_start_inhibit", start)
    return created


def test_with_block_holds_until_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    with power.keep_awake("render"):
        assert len(created) == 1
        assert created[0].closed is False
    assert created[0].closed is True


def test_exception_and_cancel_release(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"), power.keep_awake("render"):
        raise RuntimeError("boom")
    assert created[0].closed is True

    def generate():
        with power.keep_awake("render"):
            yield

    generator = generate()
    next(generator)
    generator.close()
    assert created[1].closed is True


def test_nested_jobs_share_one_inhibit(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    with power.keep_awake("outer"), power.keep_awake("inner", display=True):
        assert len(created) == 1
        assert True in created[0].displays
    assert created[0].closed is True
    with power.keep_awake("next"):
        assert len(created) == 2
    assert created[1].closed is True


def test_threads_share_one_inhibit(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    barrier = threading.Barrier(4)

    def job() -> None:
        with power.keep_awake("job"):
            barrier.wait(timeout=5)

    threads = [threading.Thread(target=job) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(created) == 1
    assert created[0].closed is True


def test_same_lock_is_reentrant(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    lock = power.keep_awake("render")
    lock.acquire()
    lock.acquire()
    lock.release()
    assert created[0].closed is False
    lock.release()
    assert created[0].closed is True
    lock.release()
    assert created[0].closed is True


def test_release_failure_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom(_Count):
        def close(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(power, "_start_inhibit", lambda reason, display: Boom())
    with power.keep_awake("render"):
        pass


def test_setting_false_skips_the_inhibit(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_counter(monkeypatch)
    assert keep_awake_during_jobs() is True
    assert not file().exists()
    set_keep_awake_during_jobs(False)
    assert values()["keep_awake_during_jobs"] is False
    with power.keep_awake("render", display=True):
        pass
    assert created == []
    set_keep_awake_during_jobs(True)
    with power.keep_awake("render"):
        assert len(created) == 1
    with pytest.raises(ValueError):
        set_keep_awake_during_jobs("no")  # type: ignore[arg-type]


def test_invalid_stored_setting_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    file().parent.mkdir(parents=True, exist_ok=True)
    file().write_text('{"keep_awake_during_jobs": "no"}\n', encoding="utf-8")
    assert keep_awake_during_jobs() is True
    created = _install_counter(monkeypatch)
    with power.keep_awake("render"):
        assert len(created) == 1


def test_windows_keeper_thread_sets_and_clears_execution_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    def record(flags: int) -> None:
        calls.append((flags, threading.get_ident()))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(power, "_set_thread_execution_state", record)
    monkeypatch.setattr(power, "_start_inhibit", _REAL_START)
    caller = threading.get_ident()
    with power.keep_awake("render"):
        assert calls
        assert calls[0][0] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        assert calls[0][1] != caller
        with power.keep_awake("preview", display=True):
            assert calls[-1][0] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        assert calls[-1][0] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    assert calls[-1] == (ES_CONTINUOUS, calls[-1][1])
    assert all(thread_id != caller for _, thread_id in calls)


def test_windows_failure_logs_and_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(flags: int) -> None:
        raise OSError("SetThreadExecutionState failed")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(power, "_set_thread_execution_state", boom)
    monkeypatch.setattr(power, "_start_inhibit", _REAL_START)
    with caplog.at_level(logging.WARNING), power.keep_awake("render"):
        pass
    assert "keep_awake" in caplog.text


def test_linux_uses_login1_and_holds_the_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    jeepney = pytest.importorskip("jeepney")
    raw = os.open(os.devnull, os.O_RDONLY)
    descriptor = jeepney.FileDescriptor(raw)
    sent: list[object] = []
    closed = {"connection": False}

    class Connection:
        def send_and_get_reply(self, message: object, timeout: float | None = None) -> object:
            sent.append(message)
            return SimpleNamespace(
                header=SimpleNamespace(message_type=jeepney.MessageType.method_return),
                body=(descriptor,),
            )

        def close(self) -> None:
            closed["connection"] = True

    def open_connection(*args: object, **kwargs: object) -> Connection:
        assert kwargs["bus"] == "SYSTEM"
        assert kwargs["enable_fds"] is True
        return Connection()

    monkeypatch.setattr(power, "open_dbus_connection", open_connection)
    hold = power._inhibit_login1("render")
    message = sent[0]
    assert message.body == ("sleep:idle", "Voxprint", "render", "block")  # type: ignore[attr-defined]
    assert closed["connection"] is True
    descriptor.fileno()
    hold.close()
    with pytest.raises(jeepney.NoFDError):
        descriptor.fileno()


def test_linux_fallback_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def fail(name: str):
        def attempt(reason: str) -> _Count:
            order.append(name)
            raise RuntimeError(name)

        return attempt

    screen = _Count()

    def screensaver(reason: str) -> _Count:
        order.append("screensaver")
        assert reason == "render"
        return screen

    monkeypatch.setattr(power, "_inhibit_login1", fail("login1"))
    monkeypatch.setattr(power, "_inhibit_systemd", fail("systemd"))
    monkeypatch.setattr(power, "_inhibit_screensaver", screensaver)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(power, "_start_inhibit", _REAL_START)
    with power.keep_awake("render"):
        assert order == ["login1", "systemd", "screensaver"]
    assert screen.closed is True


def test_linux_all_backends_log_a_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(reason: str) -> _Count:
        raise RuntimeError("unavailable")

    monkeypatch.setattr(power, "_inhibit_login1", fail)
    monkeypatch.setattr(power, "_inhibit_systemd", fail)
    monkeypatch.setattr(power, "_inhibit_screensaver", fail)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(power, "_start_inhibit", _REAL_START)
    with caplog.at_level(logging.WARNING), power.keep_awake("render"):
        pass
    assert "keep_awake" in caplog.text


def test_systemd_inhibit_command_is_held_until_release(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        def __init__(self, args: list[str], **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs
            self.returncode: int | None = None
            self.terminated = False

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is None:
                raise subprocess.TimeoutExpired(self.args, timeout or 0)
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

    launched: list[Proc] = []

    def popen(args: list[str], **kwargs: object) -> Proc:
        proc = Proc(args, **kwargs)
        launched.append(proc)
        return proc

    monkeypatch.setattr(power.subprocess, "Popen", popen)
    hold = power._inhibit_systemd("render")
    assert launched[0].args[:4] == [
        "systemd-inhibit",
        "--what=sleep:idle",
        "--who=Voxprint",
        "--why",
    ]
    assert "render" in launched[0].args
    assert "--mode=block" in launched[0].args
    assert launched[0].terminated is False
    hold.close()
    assert launched[0].terminated is True


def test_screensaver_uninhibit_uses_the_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    jeepney = pytest.importorskip("jeepney")
    calls: list[tuple[str, object]] = []

    class Connection:
        def send_and_get_reply(self, message: object, timeout: float | None = None) -> object:
            member = message.header.fields[jeepney.HeaderFields.member]  # type: ignore[attr-defined]
            calls.append((member, message.body))  # type: ignore[attr-defined]
            if member == "Inhibit":
                return SimpleNamespace(
                    header=SimpleNamespace(message_type=jeepney.MessageType.method_return),
                    body=(7,),
                )
            return SimpleNamespace(
                header=SimpleNamespace(message_type=jeepney.MessageType.method_return),
                body=(),
            )

        def close(self) -> None:
            calls.append(("close", ()))

    monkeypatch.setattr(power, "open_dbus_connection", lambda *args, **kwargs: Connection())
    hold = power._inhibit_screensaver("render")
    assert calls == [("Inhibit", ("Voxprint", "render"))]
    hold.close()
    assert calls[1] == ("UnInhibit", (7,))
    assert calls[2][0] == "close"
