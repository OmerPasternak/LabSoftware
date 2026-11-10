"""One analyzer, with no intermediate Python window."""
from pathlib import Path
from PyQt6.QtCore import QProcess
from hhg_control.hhg_analysis import window
from hhg_control.hhg_analysis.matlab_bridge import matlab_arguments


def test_direct_launch_has_no_intermediate_widget(qtbot, monkeypatch, tmp_path):
    exe=tmp_path/'matlab.exe'; exe.touch()
    monkeypatch.setattr(window, 'matlab_candidates', lambda: [exe])
    class Settings:
        def __init__(self,*args): pass
        def value(self,*args): return ''
        def setValue(self,*args): pass
    monkeypatch.setattr(window,'QSettings',Settings)
    monkeypatch.setattr(window.QStandardPaths,'writableLocation',lambda *_: str(tmp_path))
    calls=[]
    monkeypatch.setattr(QProcess,'startDetached',lambda *args: (calls.append(args) or (True,123)))
    before=set(window.QApplication.topLevelWidgets())
    assert window.main()==0
    assert set(window.QApplication.topLevelWidgets())==before
    assert len(calls)==1
    assert '-automation' in calls[0][1]
    assert "'show_scan'" in calls[0][1][-1]


def test_engine_supports_interactive_workspace_without_starting_desktop():
    args=matlab_arguments(Path('test.log'))
    assert '-desktop' not in args and '-batch' not in args
    assert '-automation' in args and '-r' in args
