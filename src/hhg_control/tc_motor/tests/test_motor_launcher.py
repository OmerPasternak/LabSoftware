"""Windows launcher routing, including spaces and an unrelated working directory."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import venv

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows BAT launcher")
def test_launcher_uses_own_checkout_with_spaces(tmp_path):
    """Use a harmless stub module to verify the actual BAT without opening a GUI."""
    checkout = tmp_path / "TC motor checkout with spaces"
    package = checkout / "src" / "hhg_control" / "tc_motor"
    (package / "gui").mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(checkout / ".venv")
    launcher = package / "run_TC_motor.bat"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (package / "gui" / "__main__.py").write_text(
        "import json, os\nprint(json.dumps({'cwd':os.getcwd(),'module':__file__}))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(launcher)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout.strip())
    assert Path(output["cwd"]).resolve() == checkout.resolve()
    assert Path(output["module"]).resolve() == (package / "gui" / "__main__.py").resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows BAT launcher")
@pytest.mark.parametrize("pip_exit", [0, 2])
def test_setup_launcher_uses_project_python_and_checks_only_after_install(tmp_path, pip_exit):
    """Stub pip and the checker: exercise routing without downloads or hardware."""
    checkout = tmp_path / "TC motor setup with spaces"
    package = checkout / "src" / "hhg_control" / "tc_motor"
    package.mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(checkout / ".venv")
    launcher = package / "run_TC_motor.bat"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (checkout / "src" / "pip.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['install', 'pyvisa>=1.14,<2']\n"
        "Path('installer.json').write_text(json.dumps({'python':sys.executable}))\n"
        f"raise SystemExit({pip_exit})\n", encoding="utf-8")
    (package / "check_environment.py").write_text(
        "from pathlib import Path\nassert Path('installer.json').exists()\n"
        "Path('backend-check-ran').touch()\n", encoding="utf-8")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(launcher), "setup"],
        cwd=tmp_path, capture_output=True, input="\n", text=True, timeout=30)
    assert result.returncode == (0 if pip_exit == 0 else 1), result.stdout + result.stderr
    installed = json.loads((checkout / "installer.json").read_text())
    assert Path(installed["python"]).resolve() == (checkout / ".venv/Scripts/python.exe").resolve()
    assert (checkout / "backend-check-ran").exists() == (pip_exit == 0)


def wait_for_file(path):
    """Bound the wait for a detached, harmless VBS-launched stub to finish."""
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert path.exists(), f"Launcher did not produce {path}"


@pytest.mark.skipif(os.name != "nt", reason="Windows VBS launcher")
def test_vbs_gui_uses_project_pythonw_without_console(tmp_path):
    checkout = tmp_path / "TC motor VBS checkout with spaces"
    package = checkout / "src" / "hhg_control" / "tc_motor"
    (package / "gui").mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(checkout / ".venv")
    launcher = package / "run_TC_motor.vbs"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (package / "gui" / "__main__.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path('vbs-result.json').write_text(json.dumps({'cwd':os.getcwd(),'python':sys.executable}))\n",
        encoding="utf-8")
    result = subprocess.run(["cscript.exe", "//nologo", str(launcher)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    path = checkout / "vbs-result.json"
    wait_for_file(path)
    data = json.loads(path.read_text())
    assert Path(data["cwd"]).resolve() == checkout.resolve()
    assert Path(data["python"]).resolve() == (checkout / ".venv/Scripts/pythonw.exe").resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows VBS launcher")
def test_vbs_setup_passes_setup_to_own_bat_with_spaces(tmp_path):
    folder = tmp_path / "TC motor setup VBS with spaces"
    folder.mkdir()
    launcher = folder / "setup_physical_mode.vbs"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (folder / "run_TC_motor.bat").write_text(
        '@echo off\necho %~1>"%~dp0setup_request.txt"\n', encoding="utf-8")
    result = subprocess.run(["cscript.exe", "//nologo", str(launcher)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    path = folder / "setup_request.txt"
    wait_for_file(path)
    assert path.read_text().strip() == "setup"


@pytest.mark.skipif(os.name != "nt", reason="Windows VBS launcher")
def test_vbs_link_check_passes_numeric_com_to_own_bat(tmp_path):
    """Run only harmless launcher stubs; a numeric argument avoids InputBox UI."""
    folder = tmp_path / "TC motor link VBS with spaces"
    folder.mkdir()
    launcher = folder / "check_serial_link.vbs"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (folder / "run_TC_motor.bat").write_text(
        '@echo off\necho %~1 %~2>"%~dp0link_request.txt"\n', encoding="utf-8")
    result = subprocess.run(["cscript.exe", "//nologo", str(launcher), "21"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    path = folder / "link_request.txt"
    wait_for_file(path)
    assert path.read_text().strip() == "check-link 21"


@pytest.mark.skipif(os.name != "nt", reason="Windows BAT launcher")
def test_bat_link_check_uses_project_python_with_spaces(tmp_path):
    """No hardware: a stub captures module arguments and interpreter identity."""
    checkout = tmp_path / "TC motor link checkout with spaces"
    package = checkout / "src" / "hhg_control" / "tc_motor"
    package.mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(checkout / ".venv")
    launcher = package / "run_TC_motor.bat"
    shutil.copyfile(Path(__file__).resolve().parents[1] / launcher.name, launcher)
    (package / "check_serial_link.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path('link-result.json').write_text(json.dumps({'args':sys.argv[1:],'python':sys.executable}))\n",
        encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", "call", str(launcher), "check-link", "21"],
                            cwd=tmp_path, capture_output=True, input="\n", text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((checkout / "link-result.json").read_text())
    assert data["args"] == ["--physical", "--port", "21"]
    assert Path(data["python"]).resolve() == (checkout / ".venv/Scripts/python.exe").resolve()
