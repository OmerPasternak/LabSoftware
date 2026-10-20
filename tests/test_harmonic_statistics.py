"""Offline persistence, weak-peak segmentation and SI calibration tests."""
import json
from pathlib import Path
import subprocess
import sys

import h5py
import numpy as np
import pytest

from hhg_control.analysis.spectral import (
    default_harmonic_map_path, detect_harmonic_regions, save_harmonic_regions, load_harmonic_regions,
)
from hhg_control.analysis.harmonic_statistics import compute_si_harmonic_g2, fit_mcp_intercept


def test_reference_is_persistent_atomic_and_bad_replacement_preserves_old(tmp_path, monkeypatch):
    target = tmp_path / "settings" / "harmonics.json"
    monkeypatch.setenv("HHG_HARMONIC_MAP", str(target))
    assert default_harmonic_map_path() == target
    def save(stop):
        return save_harmonic_regions(target, [dict(label="H11.5", order=11.5, x_start=2, x_stop=stop)],
                                     tmp_path / "old.h5", (3, 20), overwrite=True)
    save(7)
    before = target.read_bytes()
    with pytest.raises(ValueError):
        save(25)
    assert target.read_bytes() == before
    save(9)
    assert load_harmonic_regions(target)["harmonics"][0]["order"] == 11.5
    assert load_harmonic_regions(target)["harmonics"][0]["x_stop"] == 9
    assert not list(target.parent.glob("*.tmp"))


def test_failed_atomic_replace_keeps_previous_map(tmp_path, monkeypatch):
    target = tmp_path / "harmonics.json"
    target.write_text("previous", encoding="utf-8")
    def deny_replace(*args):
        raise PermissionError("simulated")
    monkeypatch.setattr("hhg_control.analysis.spectral.os.replace", deny_replace)
    with pytest.raises(PermissionError):
        save_harmonic_regions(target, [dict(label="P1", x_start=1, x_stop=3)],
                              tmp_path / "data.h5", (1, 5), overwrite=True)
    assert target.read_text() == "previous"
    assert not list(tmp_path.glob("*.tmp"))


def test_detection_keeps_weak_peak_without_strong_peak_fraction_cutoff():
    x = np.arange(600)
    rng = np.random.default_rng(4)
    signal = (20 + 1000*np.exp(-0.5*((x-120)/9)**2) +
              18*np.exp(-0.5*((x-300)/7)**2) + 300*np.exp(-0.5*((x-460)/10)**2))
    regions, details = detect_harmonic_regions((signal+rng.normal(0, .5, len(x)))[None, :])
    assert len(regions) == 3
    for center, region in zip((120, 300, 460), regions):
        assert region["x_start"] < center < region["x_stop"]
        assert region["x_stop"]-region["x_start"] < 70
    assert details["requires_visual_review"]
    assert detect_harmonic_regions(np.ones((3, 100)))[0] == []


def test_si_intercept_removes_intensity_dependent_mcp_term():
    mu = np.geomspace(10, 1000, 50)
    variance = 1.3*mu**2 + 80*mu
    fit = fit_mcp_intercept(mu, variance, np.ones(50, dtype=bool))
    assert fit["status"] == "MODEL_ESTIMATE"
    assert fit["g2_light"] == pytest.approx(2.3)
    assert fit["mcp_coefficient_adu"] == pytest.approx(80)
    assert fit_mcp_intercept(np.ones(30), np.ones(30), np.ones(30, bool))["g2_light"] is None


def _matched_files(tmp_path):
    """Deterministic orthogonal shot fluctuations give exactly known mean/variance."""
    n, width = 24, 16
    rng = np.random.default_rng(8)
    q, _ = np.linalg.qr(np.column_stack([np.ones(n), rng.normal(size=(n, 3))]))
    noise = q[:, 1:]*np.sqrt(n-1)
    mu = np.geomspace(30, 900, width)
    bg = 10 + noise[:, 0, None]*2 + np.zeros((n, width))
    off = 16 + noise[:, 1, None]*3 + np.zeros((n, width))
    # Odd uses BG (var4), even uses OFF (var9); both have light g2=2.3 and k=50.
    reference_mu = np.r_[np.full(8,10.), np.full(8,16.)]
    reference_var = np.r_[np.full(8,4.), np.full(8,9.)]
    on = reference_mu + mu + noise[:, 2, None]*np.sqrt(1.3*mu**2+50*mu+reference_var)
    paths = []
    for name, data in (("on", on), ("off", off), ("bg", bg)):
        path = tmp_path / f"{name}.h5"
        with h5py.File(path, "w") as h:
            h.create_dataset("images", data=data[:,None,:])
            h.attrs["complete"] = True
            h.attrs["exposure_time_s"] = .001
        paths.append(path)
    harmonic_map = save_harmonic_regions(tmp_path / "map.json", [
        dict(label="H11", order=11., x_start=0, x_stop=8),
        dict(label="H12", order=12., x_start=8, x_stop=16),
    ], paths[0], (1,16))
    return paths, harmonic_map


def test_si_pipeline_uses_correct_reference_and_leaves_sources_unchanged(tmp_path):
    paths, harmonic_map = _matched_files(tmp_path)
    before = [p.read_bytes() for p in paths]
    report = compute_si_harmonic_g2(*paths, harmonic_map, batch_size=5, min_mean_snr=.1)
    assert [h["reference"] for h in report["harmonics"]] == ["BACKGROUND", "BSV_OFF"]
    for harmonic in report["harmonics"]:
        assert harmonic["fit"]["g2_light"] == pytest.approx(2.3, abs=1e-10)
        assert harmonic["fit"]["mcp_coefficient_adu"] == pytest.approx(50, abs=1e-9)
    assert [p.read_bytes() for p in paths] == before
    with pytest.raises(ValueError, match="distinct"):
        compute_si_harmonic_g2(paths[0], paths[0], paths[2], harmonic_map)
    with h5py.File(paths[1], "a") as h:
        h.attrs["exposure_time_s"] = .002
    with pytest.raises(ValueError, match="mismatch"):
        compute_si_harmonic_g2(*paths, harmonic_map)


def test_si_cli_uses_default_map_without_map_picker_and_saves_diagnostics(tmp_path, monkeypatch):
    paths, harmonic_map = _matched_files(tmp_path)
    monkeypatch.setenv("HHG_HARMONIC_MAP", str(harmonic_map))
    script = Path(__file__).resolve().parents[1]/"scripts"/"analyze_harmonic_g2.py"
    result = subprocess.run([sys.executable, str(script), str(paths[0]), "--bsv-off", str(paths[1]),
                             "--background", str(paths[2]), "--no-show"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(next((tmp_path/"analysis").glob("*.json")).read_text())
    assert "fit" in report["harmonics"][0]
    assert next((tmp_path/"analysis").glob("*.png")).stat().st_size > 0


def test_marker_detects_reviews_and_replaces_only_on_confirm(tmp_path, monkeypatch):
    """Exercise the real Matplotlib marker without opening a window or camera."""
    import importlib.util
    import matplotlib.pyplot as plt
    from matplotlib.backend_bases import KeyEvent
    x = np.arange(200)
    signal = 10 + 300*np.exp(-.5*((x-70)/7)**2) + 20*np.exp(-.5*((x-150)/5)**2)
    source = tmp_path/"reference.h5"
    with h5py.File(source, "w") as h:
        h.create_dataset("images", data=np.tile(signal, (3, 2, 1)))
    target = tmp_path/"default.json"
    target.write_text("old reference")
    monkeypatch.setenv("HHG_HARMONIC_MAP", str(target))
    script = Path(__file__).resolve().parents[1]/"scripts"/"mark_harmonic_limits.py"
    spec = importlib.util.spec_from_file_location("test_harmonic_marker", script)
    marker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(marker)
    def cancel(**kwargs):
        plt.close(plt.gcf())
    monkeypatch.setattr(plt, "show", cancel)
    assert marker.main([str(source)]) == 0
    assert target.read_text() == "old reference"
    def confirm(**kwargs):
        figure = plt.gcf()
        figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", figure.canvas, key="enter"))
    monkeypatch.setattr(plt, "show", confirm)
    assert marker.main([str(source)]) == 0
    saved = load_harmonic_regions(target)
    assert len(saved["harmonics"]) == 2
    assert saved["detection"]["visually_confirmed"]
    assert Path(saved["annotated_snapshot_png"]).stat().st_size > 0


@pytest.mark.parametrize('kind', ['g2', 'covariance'])
def test_analysis_opens_result_plot_by_default(tmp_path, monkeypatch, kind):
    """GUI child scripts must show calculated figures without an extra file search."""
    import importlib.util
    import matplotlib.pyplot as plt
    paths, harmonic_map = _matched_files(tmp_path)
    monkeypatch.setenv('HHG_HARMONIC_MAP', str(harmonic_map))
    name = 'analyze_harmonic_g2.py' if kind == 'g2' else 'analyze_spectral_covariance.py'
    script = Path(__file__).resolve().parents[1]/'scripts'/name
    spec = importlib.util.spec_from_file_location(f'popup_{kind}', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shown = []
    def show():
        shown.extend(plt.get_fignums())
        plt.close('all')
    monkeypatch.setattr(module, 'show_analysis_figures', show)
    args = [str(paths[0])]
    if kind == 'g2':
        args += ['--bsv-off', str(paths[1]), '--background', str(paths[2])]
    assert module.main(args) == 0
    assert shown


def test_result_window_is_visible_and_show_blocks_child_process(monkeypatch):
    """Show actual native Qt windows before entering the child plotting loop."""
    import matplotlib.pyplot as plt
    from hhg_control.analysis.plot_windows import show_analysis_figures
    figure, axis = plt.subplots()
    axis.plot([1,2])
    calls = []
    def show(*, block):
        calls.append(block)
        assert figure.canvas.manager.window.isVisible()
    monkeypatch.setattr(plt, 'show', show)
    try:
        show_analysis_figures()
        assert calls == [True]
    finally:
        plt.close(figure)
