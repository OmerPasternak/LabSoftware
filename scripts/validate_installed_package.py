"""Build a clean source export and validate an isolated installed wheel offline.

No camera/motor is connected. Dependencies may be obtained by pip; the working
lab environment is never changed. Run explicitly; this is separate from pytest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def run(root: Path, output: Path) -> dict:
    """Export proposed files, install only declared test dependencies, smoke GUIs."""
    output.mkdir(parents=True, exist_ok=False)
    source=output/'source'; source.mkdir()
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
    for name in filter(None,names):
        path=root/name
        if not path.is_file(): continue
        target=source/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,target)
    environment=dict(os.environ,QT_QPA_PLATFORM='offscreen')
    environment.pop('PYTHONPATH',None)
    wheels=output/'wheels'; wheels.mkdir()
    def command(args,cwd):
        completed=subprocess.run(args,cwd=cwd,env=environment,capture_output=True,text=True,timeout=300)
        with (output/'commands.log').open('a',encoding='utf-8') as handle:
            handle.write(str(args)+'\n'+completed.stdout+completed.stderr+'\n')
        if completed.returncode: raise RuntimeError(f"Command failed ({completed.returncode}); see {output/'commands.log'}")
        return completed.stdout
    command([sys.executable,'-m','pip','wheel','--no-deps','--wheel-dir',str(wheels),str(source)],output)
    isolated=output/'isolated'; venv.EnvBuilder(with_pip=True).create(isolated)
    python=isolated/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    wheel=next(wheels.glob('hhg_control-*.whl'))
    command([str(python),'-m','pip','install',str(wheel)+'[test]'],output)
    empty=output/'empty';empty.mkdir()
    resources={}
    for path in (source/'src/HHG analysis').rglob('*'):
        if path.is_file() and not path.name.startswith("."):
            resources[str(path.relative_to(source/'src/HHG analysis')).replace('\\','/')]=hashlib.sha256(path.read_bytes()).hexdigest()
    resource_manifest=output/"resources.json"
    resource_manifest.write_text(json.dumps(resources),encoding="utf-8")
    probe="""
import hashlib, importlib, importlib.metadata, json, pathlib, sys
package=importlib.import_module('hhg_control.hhg_analysis')
base=pathlib.Path(package.__file__).parent
assert 'site-packages' in str(base), base
for name,expected in RESOURCES.items():
    assert hashlib.sha256((base/name).read_bytes()).hexdigest()==expected,name
for old,new in [('hhg_control.drivers.pco_edge','hhg_control.camera.drivers.pco_edge'),('hhg_control.ui.camera.camera_panel','hhg_control.gui.camera.camera_panel'),('hhg_control.gui.fundamental_analysis.window','hhg_control.hhg_analysis.window')]:
    assert importlib.import_module(old) is importlib.import_module(new)
entries={ep.name:ep for ep in importlib.metadata.distribution('hhg-control').entry_points if ep.group=='console_scripts'}
assert set(entries)=={'hhg-camera','hhg-tc-motor','hhg-analysis'}
assert not importlib.util.find_spec('pco')
print(json.dumps({'resources_verified':len(RESOURCES),'entrypoints':sorted(entries),'installed_from':str(base)}))
""".replace('RESOURCES',f"json.loads(pathlib.Path({str(resource_manifest)!r}).read_text(encoding='utf-8'))")
    result=json.loads(command([str(python),'-c',probe],empty).strip().splitlines()[-1])
    # Exercise each actual entrypoint in its own process, initially disconnected.
    for name in ('hhg-camera','hhg-tc-motor','hhg-analysis'):
        gui="""
import importlib, importlib.metadata
from PyQt6.QtCore import QTimer
entry=next(ep for ep in importlib.metadata.distribution('hhg-control').entry_points if ep.name==NAME)
module=importlib.import_module(entry.value.split(':')[0])
original=module.QApplication
class AutoCloseApp(original):
    def __init__(self,*args):
        super().__init__(*args)
        QTimer.singleShot(300,lambda:[w.close() for w in self.topLevelWidgets()])
module.QApplication=AutoCloseApp
entry.load()()
""".replace('NAME',repr(name))
        command([str(python),'-c',gui],empty)
    result['source_export']=str(source)
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(run(Path(__file__).resolve().parents[1],args.output.resolve()),indent=2))
