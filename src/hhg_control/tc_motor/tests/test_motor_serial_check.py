"""Offline query-only diagnostic checks, including failed reception and cleanup."""

import json
from types import SimpleNamespace

import pytest

from hhg_control.tc_motor import check_serial_link as check


class LinkPeer:
    """Synthetic text replies with controllable status and disconnect failure."""

    def __init__(self, *, fail_at=0, close_fails=False, status="000032"):
        self.calls = []
        self.fail_at = fail_at
        self.close_fails = close_fails
        self.status = status
        self.closed = False
        self.session_details = {"visa_library": "synthetic backend"}
        self.last_exchange = {}

    def open(self):
        pass

    def query(self, command):
        self.calls.append(command)
        self.last_exchange = {"command": command, "rx_hex": "synthetic only"}
        if len(self.calls) == self.fail_at:
            raise OSError("synthetic corrupt reply: b1 54 53")
        values = {"VE": "SMC_CC synthetic", "ZX?": "LTA-HS", "TS": self.status,
                  "TP": "2", "SU?": "0.0000353932", "VA?": "5", "AC?": "20",
                  "SL?": "0", "SR?": "50", "BA?": "0", "BH?": "0"}
        return "1" + command[1:3] + values[command[1:]]

    def close(self):
        if self.close_fails:
            raise OSError("synthetic close failure")
        self.closed = True


@pytest.fixture(autouse=True)
def virtual_time(monkeypatch):
    now = [0.0]

    def sleep(seconds):
        assert seconds > 0
        now[0] += seconds

    monkeypatch.setattr(check, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))


@pytest.mark.parametrize('status', ["000028"])
def test_check_is_bounded_and_never_commands_motion_or_configuration(status):
    peer = LinkPeer(status=status)
    report = check.inspect_serial_link(peer, duration_s=1)
    assert report["ok"] and report["port_released"] and peer.closed
    assert report["successful_snapshots"] == 2
    assert report["completed_duration_s"] == 1
    assert not report["standstill_verified"]
    assert all(command[1:] in {"VE", "ZX?", "SU?", "VA?", "AC?", "SL?", "SR?", "BA?", "BH?", "TS", "TP"}
               for command in peer.calls)
    assert report["native_settings"]["SU"] == "0.0000353932"
    assert report["transport"]["visa_library"] == "synthetic backend"


@pytest.mark.parametrize('failure', [1])
def test_failed_link_always_returns_report_closes_and_does_not_retry(failure):
    peer = LinkPeer(fail_at=failure)
    report = check.inspect_serial_link(peer, duration_s=1)
    assert not report["ok"] and peer.closed
    assert len(peer.calls) == failure
    assert "b1 54 53" in report["error"]
    assert "error" in report["exchanges"][-1]
    assert report["exchanges"][-1]["wire"]["command"] == peer.calls[-1]




def test_malformed_status_is_not_a_passing_connection_check():
    peer = LinkPeer(status="123")
    report = check.inspect_serial_link(peer, duration_s=1)
    assert not report["ok"] and peer.closed
    assert "Malformed status" in report["error"]
    assert peer.calls[-1] == "1TS"


@pytest.mark.parametrize('kwargs', [{"axis": True}, {"duration_s": float("nan")}])
def test_bad_inputs_never_open_transport(kwargs):
    peer = LinkPeer()
    peer.open = lambda: pytest.fail("Invalid request opened transport")
    with pytest.raises(ValueError):
        check.inspect_serial_link(peer, **kwargs)


def test_cli_requires_explicit_physical_authorization(monkeypatch):
    monkeypatch.setattr(check, "VisaSerialTransport", lambda *_: pytest.fail("No authorization"))
    with pytest.raises(SystemExit) as error:
        check.main(["--port", "21"])
    assert error.value.code == 2


def test_cli_saves_report_after_failed_reception(tmp_path, monkeypatch):
    peer = LinkPeer(fail_at=10)
    monkeypatch.setattr(check, "VisaSerialTransport", lambda resource: peer)
    output = tmp_path / "motor report with spaces.json"
    assert check.main(["--physical", "--port", "21", "--duration", "1", "--output", str(output)]) == 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert not saved["ok"] and saved["port_released"]
    assert "b1 54 53" in saved["error"]


def test_comparison_closes_visa_before_explicit_direct_test(tmp_path, monkeypatch):
    first = LinkPeer(fail_at=12)
    second = LinkPeer()
    monkeypatch.setattr(check, "VisaSerialTransport", lambda resource: first)

    def direct(resource):
        assert first.closed
        return second

    monkeypatch.setattr(check, "DirectSerialTransport", direct)
    path = tmp_path / "comparison.json"
    result = check.main(["--physical", "--port", "21", "--duration", "1",
                         "--transport", "compare", "--output", str(path)])
    report = json.loads(path.read_text())
    assert result == 0 and report["passing_transports"] == ["serial"]
    assert len(report["runs"]) == 2 and report["port_released"]
    assert second.closed


def test_comparison_never_opens_another_backend_after_cleanup_failure(tmp_path, monkeypatch):
    first = LinkPeer(close_fails=True)
    monkeypatch.setattr(check, "VisaSerialTransport", lambda resource: first)
    monkeypatch.setattr(check, "DirectSerialTransport", lambda resource: pytest.fail("Previous port is still owned"))
    path = tmp_path / "blocked.json"
    assert check.main(["--physical", "--port", "21", "--duration", "1",
                       "--transport", "compare", "--output", str(path)]) == 1
    assert len(json.loads(path.read_text())["runs"]) == 1
