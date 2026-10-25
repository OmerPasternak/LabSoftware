# Project layout and stable entry points

The shared project root contains `AGENTS.md`, `GEMINI.md`, `README.md`,
`pyproject.toml`, the launchers, `docs/`, `scripts/`, `tests/` and
`application tests/`. Keep these shared resources at the root so notebooks,
analysis tools and future instruments can find them without hardcoded paths.

```text
src/hhg_control/
  gui/                     instrument interfaces
    camera/                existing camera window, workers and selections
  camera/                  camera instrument implementation
    drivers/               abstract camera, physical PCO driver, mock, defaults
    sequencer/             camera acquisition, buffering, integrity and writing
    analysis/              read-only saved-camera analysis
  tc_motor/                self-contained TC motor (LTA-HS/SMC100) feature
    drivers/               abstract motor, project mock, physical adapter
    sequencer.py           bounded moves and reusable measurement loops
    gui/                   separate motor window and background worker
    tests/                 offline motor and Qt regression tests
    docs/                  motor guide, profile template, and agent handoff
    run_TC_motor.bat       launcher resolving this checkout from its own path
  scan/                    independent motor–camera experiment coordinator
    plan.py                ordered visits, repeats and explicit mm/fs calibration
    sequencer.py           finite recordings, metadata, STOP and attempt recovery
    session.py             explicit instrument construction and safe cleanup
    gui.py                 separate scan window and owning worker
    tests/                 offline ordering/attribution/recovery/Qt checks
  ownership.py             nonblocking sequencer session reservations
  safe_io.py               shared filesystem/resource checks
  project_paths.py         checkout-resource discovery, independent of cwd
```

No acquisition logic, numerical processing, hardware settings, data format or
GUI appearance changes are part of this reorganization. The raw-data integrity
policy still applies to every instrument.

For another instrument, add its implementation beside `camera/` and its
interface under `gui/`. Preserve the driver/mock/sequencer boundaries. Do not
put physical instrument logic into GUI code or modify the camera package just
to register an unrelated instrument. Shared orchestration can be added as a
separate feature when its experiment requirements are defined.

## Compatibility

Old imports under `hhg_control.drivers`, `hhg_control.sequencer`,
`hhg_control.analysis` and `hhg_control.ui.camera` remain compatibility aliases.
They resolve to the same module/class objects, including monkeypatches and
spawned-process imports. Edit canonical implementations in `camera/` or `gui/`,
not the alias files. Existing notebooks/scripts keep their current import paths.

`run_scan.bat` / `python -m hhg_control.scan` opens the independent scan window;
see [motor–camera scan](motor_camera_scan.md). No camera-window registration or
layout changes are required to use the shared sequencers.

`run_camera_gui.bat` and `run_camera_gui.vbs` resolve the checkout from their own
location and launch `python -m hhg_control.gui.camera`. The installed
`hhg-camera` entry point uses the canonical package. The old
`python -m hhg_control.ui.camera_gui` entry still works.

Analysis scripts and notebooks remain at their current project-relative paths.
Resource lookup scans for the checkout's `pyproject.toml` and `src/hhg_control`
instead of depending on package depth. User data locations and the per-user
harmonic map are not relocated by the code reorganization.
