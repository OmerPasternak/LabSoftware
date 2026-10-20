"""Legacy names and relocated source must share identity and discover checkout resources."""
import importlib
from pathlib import Path
import pytest


@pytest.mark.parametrize('old,new',[
    ('drivers.base_camera','camera.drivers.base_camera'),
    ('drivers.mock_camera','camera.drivers.mock_camera'),
    ('drivers.pco_edge','camera.drivers.pco_edge'),
    ('sequencer.scan_manager','camera.sequencer.scan_manager'),
    ('sequencer.capture_pipeline','camera.sequencer.capture_pipeline'),
    ('analysis.spectral','camera.analysis.spectral'),
    ('analysis.harmonic_statistics','camera.analysis.harmonic_statistics'),
    ('ui.camera.camera_panel','gui.camera.camera_panel'),
    ('ui.camera.workers','gui.camera.workers'),
])
def test_old_and_new_imports_are_the_same_module(old,new):
    assert importlib.import_module('hhg_control.'+old) is importlib.import_module('hhg_control.'+new)


def test_shared_resources_resolve_independently_of_working_directory(tmp_path,monkeypatch):
    from hhg_control.project_paths import project_root
    monkeypatch.chdir(tmp_path)
    root=project_root()
    assert root == Path(__file__).resolve().parents[1]
    assert (root/'AGENTS.md').is_file()
    assert (root/'docs'/'raw_data_integrity_policy.md').is_file()
    assert (root/'scripts'/'analyze_spectral_covariance.py').is_file()


def test_old_driver_monkeypatch_is_visible_to_new_code(monkeypatch):
    legacy=importlib.import_module('hhg_control.drivers.pco_edge')
    canonical=importlib.import_module('hhg_control.camera.drivers.pco_edge')
    sentinel=object()
    monkeypatch.setattr(legacy,'pco',sentinel)
    assert canonical.pco is sentinel
