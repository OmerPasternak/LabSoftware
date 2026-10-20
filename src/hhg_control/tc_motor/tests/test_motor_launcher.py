"""Windows launcher routing, including spaces and an unrelated working directory."""

import json
import os
from pathlib import Path
import shutil
import subprocess
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
