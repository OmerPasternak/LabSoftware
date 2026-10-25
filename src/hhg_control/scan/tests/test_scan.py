"""Mock-only ordering, late transfer, attribution, cancellation and recovery acceptance."""

import json
from pathlib import Path
from queue import Queue
import threading

import h5py
import numpy as np
import pytest

from hhg_control.camera.drivers.mock_camera import MockPcoCamera
from hhg_control.camera.sequencer import capture_pipeline as pipeline
from hhg_control.camera.sequencer.scan_manager import AcquisitionAborted, CameraScanManager
from hhg_control.scan import MotorCameraScan, ScanPlan, Visit
from hhg_control.safe_io import filesystem_path
from hhg_control.tc_motor.drivers.base import MotorLimits
from hhg_control.tc_motor.drivers.mock import MockMotor
from hhg_control.tc_motor.sequencer import MotorSequencer


class InstantMotor(MockMotor):
    """Synthetic driver with immediate travel; existing motor tests cover motion dynamics."""

    def __init__(self):
        super().__init__(MotorLimits(0, 10, 1, 2, 2))
        self.moves = []

    def move_to(self, position):
        super().move_to(position)
        self._position, self._profile = position, None
        self.moves.append(position)


class TaggedCamera(MockPcoCamera):
    """Frames encode exposure position; simulated transfer can arrive much later."""

    def __init__(self, motor):
        super().__init__()
        self.motor, self.recordings, self.originals = motor, [], []
        self.exposed, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.active = False
        self.gap = self.fail = False
        self.returned = 0

    def connect(self):
        self._is_connected = True
        self._roi = (0, 1072, 64, 1088)

    def confirm_recording_stopped(self):
        super().confirm_recording_stopped()
        if self.active:
            raise RuntimeError("Test recording still active.")

    def iter_frames(self, num_frames, batch_size=4, stop_check=None):
        position = self.motor.get_position()
        self.recordings.append(position)
        self.active = True
        try:
            for start in range(0, num_frames, batch_size):
                if stop_check is not None and stop_check():
                    return
                count = min(batch_size, num_frames - start)
                frames = np.stack([np.full((16, 64), int(position * 1000) + i,
                                   dtype=np.uint16) for i in range(start, start + count)])
                metadata = [{"frame_id": i + (1 if self.gap and i > 0 else 0),
                             "exposure_position_mm": position, "timestamp_source": "synthetic_test"}
                            for i in range(start, start + count)]
                self.exposed.set()
                assert self.release.wait(10), "Test failed to release late camera transfer."
                self.returned += count
                self.originals.append(frames.copy())
                yield frames, metadata
                if self.fail:
                    raise TimeoutError("Synthetic camera transfer timeout")
        finally:
            self.active = False


@pytest.fixture
def rig(tmp_path):
    motor = InstantMotor()
    sequence = MotorSequencer(motor)
    sequence.connect()
    camera = TaggedCamera(motor)
    camera.connect()
    manager = CameraScanManager(camera, tmp_path)
    scan = MotorCameraScan(sequence, manager)
    yield scan, motor, camera, manager
    camera.release.set()
    sequence.disconnect()
    manager.disconnect()


def plan(positions=(.1, .2), **kwargs):
    return ScanPlan(tuple(Visit(p) for p in positions), frames_per_point=5,
                    settle_s=0, position_tolerance_mm=.001, **kwargs)


def manifest(run):
    return json.loads((run / "run.json").read_text())


def background(action):
    results = Queue()
    def run():
        try:
            results.put(action())
        except BaseException as error:
            results.put(error)
    worker = threading.Thread(target=run)
    worker.start()
    return worker, results


def test_reverse_repeats_frame_ids_and_raw_pixels_are_bound_to_recording(rig):
    scan, motor, camera, manager = rig
    run = scan.run(plan((.1, .2, .1), repeats=2, zero_mm=.1, fs_per_mm=-1000))
    report = manifest(run)
    assert report["status"] == "COMPLETE"
    assert report["performance"]["verified_frames"] == 30
    assert report["performance"]["active_execution_s"] > 0
    assert report["performance"]["effective_fps"] == pytest.approx(
        report["performance"]["verified_frames"] / report["performance"]["active_execution_s"]
    )
    assert "motor actions" in report["performance"]["definition"]
    assert motor.moves == camera.recordings == [.1, .2, .1, .1, .2, .1]
    identities = set()
    for index, point in enumerate(report["points"]):
        attempt = point["attempts"][0]
        path = Path(attempt["file"])
        assert attempt["verification"]["recording_context_verified"]
        assert attempt["pipeline"]["files_verified"] == 1
        with h5py.File(filesystem_path(path)) as data:
            context = json.loads(data.attrs["recording_context_json"])
            identities.add(context["recording_id"])
            assert context["point_index"] == index
            assert context["repeat_index"] == index // 3
            assert context["position_unit"] == data.attrs["scan_parameter_unit"] == "mm"
            assert data.attrs["schema_version"] == "2.0"
            for frame_index, item in enumerate(data["frame_metadata/json"]):
                meta = json.loads(item)
                assert meta["recording_context"] == context
                assert meta["frame_id"] == frame_index  # Each finite recorder resets IDs.
                assert meta["exposure_position_mm"] == context["target_position_mm"]
                assert np.all(data["images"][frame_index] == int(context["target_position_mm"] * 1000) + frame_index)
    assert len(identities) == 6
    assert manager.storage_dir == run.parent
    assert motor.get_position() == .1


def test_late_transfer_reserves_both_instruments_and_blocks_next_move(rig):
    scan, motor, camera, manager = rig
    camera.release.clear()
    worker, result = background(lambda: scan.run(plan()))
    try:
        assert camera.exposed.wait(15)
        assert motor.moves == [.1]
        with pytest.raises(RuntimeError, match="reserved"):
            scan.motor.move_to(.9)
        with pytest.raises(RuntimeError, match="reserved"):
            manager.start_live()
        with pytest.raises(RuntimeError, match="reserved"):
            manager.set_storage_dir(manager.storage_dir / "wrong")
        assert motor.get_position() == .1
    finally:
        camera.release.set()
        worker.join(30)
    assert not worker.is_alive()
    run = result.get_nowait()
    assert isinstance(run, Path), run
    assert camera.recordings == [.1, .2]


def test_next_move_waits_for_verification_not_only_file_close(rig, monkeypatch):
    scan, motor, camera, manager = rig
    checking, release = threading.Event(), threading.Event()
    original = pipeline.ClosedFilePipeline.finish
    def paused(storage, *args, **kwargs):
        if not checking.is_set():
            checking.set()
            assert release.wait(10)
        return original(storage, *args, **kwargs)
    monkeypatch.setattr(pipeline.ClosedFilePipeline, "finish", paused)
    worker, result = background(lambda: scan.run(plan()))
    try:
        assert checking.wait(15)
        assert not camera.active
        assert motor.moves == [.1]
        assert len(list(manager.storage_dir.rglob("*.h5"))) == 1
    finally:
        release.set()
        worker.join(30)
    assert not worker.is_alive()
    assert isinstance(result.get_nowait(), Path)


def test_stop_during_transfer_saves_returned_batch_with_original_delay(rig):
    scan, motor, camera, manager = rig
    camera.release.clear()
    worker, result = background(lambda: scan.run(plan()))
    try:
        assert camera.exposed.wait(15)
        scan.request_stop()
    finally:
        camera.release.set()
        worker.join(30)
    assert not worker.is_alive()
    assert isinstance(result.get_nowait(), AcquisitionAborted)
    assert motor.moves == [.1]
    partial, = manager.storage_dir.rglob("*.h5.partial")
    with h5py.File(filesystem_path(partial)) as data:
        assert not data.attrs["complete"]
        assert data.attrs["frames_written"] == camera.returned == 4
        assert np.array_equal(data["images"][:4], camera.originals[0])
        assert all(json.loads(item)["recording_context"]["target_position_mm"] == .1
                   for item in data["frame_metadata/json"][:4])
    assert manifest(scan.last_run_dir)["status"] == "CANCELLED"


@pytest.mark.parametrize("fault", ["gap", "fail"])
def test_camera_fault_retains_returned_pixels_and_prevents_next_move(rig, fault):
    scan, motor, camera, manager = rig
    setattr(camera, fault, True)
    with pytest.raises((RuntimeError, TimeoutError)):
        scan.run(plan())
    assert motor.moves == [.1]
    partial, = manager.storage_dir.rglob("*.h5.partial")
    with h5py.File(filesystem_path(partial)) as data:
        assert data.attrs["frames_written"] == camera.returned == 4
        assert np.array_equal(data["images"][:4], camera.originals[0])
    assert manifest(scan.last_run_dir)["status"] == "FAILED"


def test_attribution_corruption_is_detected_before_another_move(rig, monkeypatch):
    scan, motor, camera, manager = rig
    original = pipeline.ClosedFilePipeline.submit
    def corrupt(storage, path, manifest_path, **kwargs):
        with h5py.File(filesystem_path(path), "r+") as data:
            item = json.loads(data["frame_metadata/json"][0])
            item["recording_context"]["target_position_mm"] = 99
            data["frame_metadata/json"][0] = json.dumps(item)
        return original(storage, path, manifest_path, **kwargs)
    monkeypatch.setattr(pipeline.ClosedFilePipeline, "submit", corrupt)
    with pytest.raises(RuntimeError, match="attribution"):
        scan.run(plan())
    assert motor.moves == [.1]
    path, = manager.storage_dir.rglob("*.h5")
    assert json.loads(filesystem_path(path.with_suffix(".verification.json")).read_text())["status"] == "FAILED"


def test_invalid_whole_path_is_rejected_before_any_move(rig):
    scan, motor, camera, manager = rig
    with pytest.raises(ValueError, match="per-move"):
        scan.run(plan((.1, 5)))
    assert motor.moves == camera.recordings == []


def test_recovery_skips_verified_index_and_keeps_new_attempts(rig):
    scan, motor, camera, manager = rig
    def stop_after_first(event):
        if event["state"] == "VERIFIED":
            scan.request_stop()
    requested = plan()
    with pytest.raises(AcquisitionAborted):
        scan.run(requested, on_event=stop_after_first)
    run = scan.last_run_dir
    first = manifest(run)["points"][0]["attempts"][0]["file"]
    original = filesystem_path(first).read_bytes()
    scan.acknowledge_stop()
    assert scan.run(requested, resume_directory=run) == run
    assert filesystem_path(first).read_bytes() == original
    assert motor.moves == [.1, .2]
    assert manifest(run)["status"] == "COMPLETE"


def test_move_on_same_owner_is_prohibited_during_acquisition(rig):
    scan, motor, camera, manager = rig
    def accidental_move(event):
        if event["state"] == "ACQUIRING":
            scan.motor.move_to(.2)
    with pytest.raises(ValueError, match="held"):
        scan.run(plan(), on_event=accidental_move)
    assert motor.moves == [.1]
    assert not camera.recordings


def test_recording_timeout_drains_late_return_before_reporting_failure(rig):
    scan, motor, camera, manager = rig
    camera.release.clear()
    worker, result = background(lambda: scan.run(plan(capture_timeout_s=.05)))
    try:
        assert camera.exposed.wait(15)
        # A deterministic event confirms the reader's cancellation callback
        # has seen the deadline, rather than asserting an exact sleep duration.
        source = manager._recording_source
        assert source.stop.wait(5)
    finally:
        camera.release.set()
        worker.join(30)
    assert not worker.is_alive()
    assert isinstance(result.get_nowait(), TimeoutError)
    assert motor.moves == [.1]
    partial, = manager.storage_dir.rglob("*.h5.partial")
    with h5py.File(filesystem_path(partial)) as data:
        assert data.attrs["frames_written"] == camera.returned == 4


def test_recovery_rechecks_verified_pixels_before_moving(rig):
    scan, motor, camera, manager = rig
    def stop_after_first(event):
        if event["state"] == "VERIFIED":
            scan.request_stop()
    requested = plan()
    with pytest.raises(AcquisitionAborted):
        scan.run(requested, on_event=stop_after_first)
    run = scan.last_run_dir
    path = manifest(run)["points"][0]["attempts"][0]["file"]
    with h5py.File(filesystem_path(path), "r+") as data:
        data["images"][0, 0, 0] ^= np.uint16(1)
    scan.acknowledge_stop()
    with pytest.raises(ValueError, match="pixels differ"):
        scan.run(requested, resume_directory=run)
    assert motor.moves == [.1]


def test_recovery_after_partial_uses_new_recording_without_overwriting(rig):
    scan, motor, camera, manager = rig
    camera.fail = True
    requested = plan((.1,))
    with pytest.raises(TimeoutError):
        scan.run(requested)
    run = scan.last_run_dir
    partial, = manager.storage_dir.rglob("*.h5.partial")
    original = filesystem_path(partial).read_bytes()
    camera.fail = False
    scan.acknowledge_stop()
    scan.run(requested, resume_directory=run)
    assert filesystem_path(partial).read_bytes() == original
    attempts = manifest(run)["points"][0]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["recording_id"] != attempts[1]["recording_id"]
    assert attempts[1]["status"] == "VERIFIED"
    completed = manifest(run)
    assert len(completed["execution_segments"]) == 2
    assert completed["performance"]["verified_frames"] == 5
    assert completed["performance"]["active_execution_s"] == pytest.approx(
        sum(segment["elapsed_s"] for segment in completed["execution_segments"])
    )


def test_same_run_cannot_be_reserved_for_two_recoveries(tmp_path):
    from hhg_control.scan.sequencer import reserve_run
    with reserve_run(tmp_path):
        with pytest.raises(OSError):
            with reserve_run(tmp_path):
                pytest.fail("Concurrent recovery acquired the same run")


def test_stop_during_hdf5_write_drains_all_sdk_returned_pixels(rig, monkeypatch):
    scan, motor, camera, manager = rig
    original = CameraScanManager._write_batch
    def stopped(writer, *args):
        scan.request_stop()
        return original(writer, *args)
    monkeypatch.setattr(CameraScanManager, "_write_batch", stopped)
    with pytest.raises(AcquisitionAborted):
        scan.run(plan())
    assert motor.moves == [.1]
    files = list(manager.storage_dir.rglob("*.h5")) + list(manager.storage_dir.rglob("*.h5.partial"))
    path, = files
    with h5py.File(filesystem_path(path)) as data:
        assert data.attrs["frames_written"] == camera.returned
        assert np.array_equal(data["images"][:camera.returned], np.concatenate(camera.originals))


def test_recovery_rejects_changed_plan_without_rewriting_original(rig):
    scan, motor, camera, manager = rig
    run = scan.run(plan((.1,)))
    original = (run / "run.json").read_bytes()
    with pytest.raises(ValueError, match="differ"):
        scan.run(plan((.2,)), resume_directory=run)
    assert (run / "run.json").read_bytes() == original
    assert motor.moves == [.1]


def test_motor_health_fault_during_acquisition_retains_pixels_and_stops(rig, monkeypatch):
    from dataclasses import replace
    scan, motor, camera, manager = rig
    camera.release.clear()
    observed_fault = threading.Event()
    snapshot = scan.motor.snapshot
    def faulted_status():
        status = snapshot()
        if camera.active:
            observed_fault.set()
            return replace(status, error_code=1)
        return status
    monkeypatch.setattr(scan.motor, "snapshot", faulted_status)
    worker, result = background(lambda: scan.run(plan()))
    try:
        assert camera.exposed.wait(15)
        assert observed_fault.wait(5)
    finally:
        camera.release.set()
        worker.join(30)
    error = result.get_nowait()
    assert isinstance(error, ValueError) and "not READY" in str(error)
    assert motor.moves == [.1]
