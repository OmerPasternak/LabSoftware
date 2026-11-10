"""Exercise the real invisible MATLAB server, not just command-line flags."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def registered_matlab():
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'Matlab.Application.Single'):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not registered_matlab(), reason='MATLAB COM server is not installed')
def test_real_server_stays_hidden_and_only_analyzer_opens(tmp_path):
    root=Path(__file__).resolve().parents[1]
    folder=str(root/'src/HHG analysis/matlab').replace("'", "''")
    command=(f"addpath('{folder}');f=hhg_analysis_app('','show_scan',false);"
             "assert(strcmp(get(f,'WindowStyle'),'normal'));"
             "assert(strcmp(get(groot,'DefaultFigureWindowStyle'),'normal'));"
             "assert(numel(findall(groot,'Type','figure'))==1);"
             "t=timer('StartDelay',3,'TimerFcn',@(~,~)close('all','force'));start(t)")
    log=tmp_path/'hidden_backend.log'
    host=Path(os.environ['SystemRoot'])/'System32/wscript.exe'
    result=subprocess.run([str(host),str(root/'src/HHG analysis/launch_matlab_hidden.vbs'),command,str(log),'quiet'],timeout=120)
    output=log.read_text(encoding='utf-16')
    assert result.returncode==0,output
    assert 'BackendVisible=0' in output
    assert 'AnalyzerReady; BackendVisible=0' in output
