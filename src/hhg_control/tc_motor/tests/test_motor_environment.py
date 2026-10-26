"""Dependency diagnostics use fake libraries and never discover or open ports."""

import sys
from types import SimpleNamespace

from hhg_control.tc_motor.check_environment import main


def test_dependency_check_identifies_missing_python_package(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pyvisa", None)
    assert main() == 1
    assert 'pip install ".[hardware-smc100]"' in capsys.readouterr().out


def test_dependency_check_identifies_missing_native_library(monkeypatch, capsys):
    def unavailable(_backend):
        raise OSError("No native VISA library")

    monkeypatch.setitem(sys.modules, "pyvisa", SimpleNamespace(
        __version__="test", ResourceManager=unavailable))
    assert main() == 1
    assert "Install or repair the native VISA runtime" in capsys.readouterr().out


def test_dependency_check_loads_library_without_opening_ports(monkeypatch, capsys):
    calls = []
    # There is deliberately no open_resource or list_resources on this manager.
    manager = SimpleNamespace(visalib="fake native VISA", close=lambda: calls.append("close"))
    monkeypatch.setitem(sys.modules, "pyvisa", SimpleNamespace(
        __version__="test", ResourceManager=lambda backend: manager))
    assert main() == 0
    assert calls == ["close"]
    assert "fake native VISA" in capsys.readouterr().out
