"""F02: every SDK-returned byte survives terminal failure, without hardware."""
import json
import threading
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from hhg_control.camera.drivers.mock_camera import MockPcoCamera
from hhg_control.camera.drivers import pco_edge
from hhg_control.camera.sequencer import capture_pipeline as pipeline
from hhg_control.camera.sequencer.recovery import RecoveryJournal
from hhg_control.camera.sequencer.scan_manager import CameraScanManager, AcquisitionAborted, AcquisitionBackpressure
from hhg_control.safe_io import filesystem_path
from tests.test_pco_driver_contract import FakePcoCamera


class TinyCamera(MockPcoCamera):
    WIDTH, HEIGHT = 64, 16


@pytest.fixture
def camera():
    camera=TinyCamera();camera.connect()
    yield camera
    camera.close()


def batch(first, count):
    pixels=np.stack([np.full((16,64),identifier,dtype=np.uint16) for identifier in range(first,first+count)])
    metadata=[dict(frame_id=identifier,camera_timestamp=f'time-{identifier}',raw_meta={'nested':[identifier]}) for identifier in range(first,first+count)]
    return pixels,metadata


def recovered_records(folder):
    records=[]
    for path in filesystem_path(folder).glob('*.recovery.hdf5.partial'):
        with h5py.File(path) as h5:
            assert not h5.attrs['complete'] and 'images' not in h5
            assert 'Unassigned' in h5.attrs['step_attribution']
            for name in sorted(h5):
                group=h5[name]
                if group.attrs['record_complete']:
                    records.append((group['pixels'][:] if 'pixels' in group and group['pixels'].ndim else group['pixels'][()] if 'pixels' in group else None,
                                    json.loads(group['metadata_json'][()]),json.loads(group.attrs['context_json'])))
    return records


def assert_all_frames_recoverable(folder, expected):
    """Independent byte/identity oracle; tolerate explicitly uncertain write overlap."""
    found={}
    for path in filesystem_path(folder).glob('*.h5*'):
        with h5py.File(path) as h5:
            if 'images' not in h5:continue
            for pixels,record in zip(h5['images'][:int(h5.attrs['frames_written'])],h5['frame_metadata/json'][:int(h5.attrs['frames_written'])]):
                found[json.loads(record)['frame_id']]=pixels
    for pixels,metadata,context in recovered_records(folder):
        assert 'recording_start' in context
        frames=pixels if pixels.ndim==3 else pixels[None]
        metadata=metadata if isinstance(metadata,list) else [metadata]
        for frame,record in zip(frames,metadata):
            identifier=record.get('frame_id',record.get('recorder image number'))
            if identifier in found:np.testing.assert_array_equal(found[identifier],frame)
            found[identifier]=frame
    assert set(found)==set(expected)
    for identifier,pixels in expected.items():
        assert found[identifier].dtype==pixels.dtype
        assert found[identifier].tobytes()==pixels.tobytes()


@pytest.mark.parametrize('failure',['terminal','hash'])
def test_reader_queued_and_dequeued_payloads_survive_before_handoff(camera,tmp_path,monkeypatch,failure):
    expected=batch(40,2)
    def frames(*args,**kwargs):
        yield expected
        if failure=='terminal':raise RuntimeError('terminal SDK check failed')
    camera.iter_frames=frames
    source=pipeline.BufferedCamera(camera,2,4,1024*1024,0,lambda:None)
    original=source.iter_frames
    def gated(*args,**kwargs):
        source.reader.start();assert source.done.wait(3)
        yield from original(*args,**kwargs)
    source.iter_frames=gated
    if failure=='hash':
        def bad_hash(*args):raise RuntimeError('source metadata hashing failed')
        monkeypatch.setattr(pipeline,'_metadata_digest',bad_hash)
    manager=CameraScanManager(source,tmp_path)
    try:
        with pytest.raises(RuntimeError,match='failed'):
            manager._acquire_and_save_step('recover',0,'delay',0,2,run_id='test',batch_size=4,queue_batches=1,abort_check=None,source=source)
        assert source.done.is_set() and not source.reader.is_alive()
        assert not list(tmp_path.glob('*.h5'))
        assert_all_frames_recoverable(tmp_path,{40:expected[0][0],41:expected[0][1]})
        assert manager.last_recovery[0]['complete'] is False
    finally:source.close()


def test_cancel_after_return_preserves_batch_before_writer_acceptance(camera,tmp_path):
    stop=threading.Event();pixels,metadata=batch(50,2)
    def frames(*args,**kwargs):
        stop.set();yield pixels,metadata
    camera.iter_frames=frames
    manager=CameraScanManager(camera,tmp_path)
    with pytest.raises(AcquisitionAborted):
        manager.acquire_and_save_step('cancel',0,'delay',0,4,abort_check=stop.is_set)
    assert_all_frames_recoverable(tmp_path,{50:pixels[0],51:pixels[1]})
    assert not list(tmp_path.glob('*.h5'))


def test_error_after_yield_keeps_writer_queued_and_later_reader_frames(camera,tmp_path,monkeypatch):
    first_saved=threading.Event();pixels,metadata=batch(60,4)
    def frames(*args,**kwargs):
        yield pixels[:2],metadata[:2]
        assert first_saved.wait(3)
        yield pixels[2:],metadata[2:]
        raise RuntimeError('final status failed after yield')
    camera.iter_frames=frames
    manager=CameraScanManager(camera,tmp_path)
    original=manager._write_batch
    def write(*args):
        original(*args);first_saved.set()
    monkeypatch.setattr(manager,'_write_batch',write)
    try:
        with pytest.raises(RuntimeError,match='final status failed'):
            manager.acquire_and_save_step('after_yield',0,'delay',0,4,batch_size=2)
        assert_all_frames_recoverable(tmp_path,{60+i:frame for i,frame in enumerate(pixels)})
        with h5py.File(filesystem_path(next(tmp_path.glob('*.h5.partial')))) as h5:assert not h5.attrs['complete']
    finally:first_saved.set()


@pytest.mark.parametrize('half_pair',[False,True])
def test_writer_failure_preserves_every_returned_frame(camera,tmp_path,monkeypatch,half_pair):
    returned={}
    def frames(*args,**kwargs):
        for identifier in range(70,78):
            pixels,metadata=batch(identifier,1);returned[identifier]=pixels[0].copy()
            yield pixels,metadata
    camera.iter_frames=frames
    manager=CameraScanManager(camera,tmp_path);original=manager._write_batch
    def write(images,metadata,start,pixels,records):
        if start==1:
            if half_pair:images[start:start+1]=pixels
            raise OSError('injected writer failure after first pair')
        original(images,metadata,start,pixels,records)
    monkeypatch.setattr(manager,'_write_batch',write)
    with pytest.raises(RuntimeError,match='injected writer failure'):
        manager.acquire_and_save_step('writer',0,'delay',0,8,batch_size=1,queue_batches=1)
    assert_all_frames_recoverable(tmp_path,returned)
    assert any(context.get('may_overlap_partial') for _,_,context in recovered_records(tmp_path))
    assert not manager.pending_recovery_payloads


@pytest.mark.parametrize('defect',['timeout','dtype','shape','gap','cancel','stop'])
def test_sdk_local_partial_and_malformed_returns_are_preserved(tmp_path,monkeypatch,defect):
    fake=FakePcoCamera('USB 3.0');fake.rec=SimpleNamespace(get_status=lambda:{})
    reads=[]
    def image(image_index=0):
        index=len(reads)
        pixels=np.full((16,64),80+index,dtype=np.uint16)
        if index==2 and defect=='dtype':pixels=pixels.astype(np.int32);pixels[0,:2]=[70000,-1]
        if index==2 and defect=='shape':pixels=pixels[:3]
        number=85 if index==2 and defect=='gap' else 80+index
        reads.append(pixels.copy())
        return pixels,{'recorder image number':number}
    fake.image=image
    def wait(**kwargs):
        if len(reads)==2 and defect in ('timeout','stop'):raise TimeoutError('SDK read timed out')
    fake.wait_for_new_image=wait
    original_stop=fake.stop
    if defect=='stop':
        def fail_stop():raise OSError('SDK stop also failed')
        fake.stop=fail_stop
    monkeypatch.setattr(pco_edge,'PCO_AVAILABLE',True)
    monkeypatch.setattr(pco_edge,'pco',SimpleNamespace(Camera=lambda **_:fake))
    camera=pco_edge.PcoEdgeCamera(serial='12345');camera.connect()
    manager=CameraScanManager(camera,tmp_path)
    try:
        with pytest.raises((RuntimeError,TimeoutError)) as error:
            manager.acquire_and_save_step('sdk',0,'delay',0,5,batch_size=4,abort_check=lambda:defect=='cancel' and len(reads)>=2)
        records=recovered_records(tmp_path)
        actual=[]
        for pixels,_,_ in records:actual.extend(pixels if pixels.ndim==3 else [pixels])
        assert len(actual)==len(reads)
        for saved,original in zip(actual,reads):
            assert saved.dtype==original.dtype and saved.shape==original.shape
            assert saved.tobytes()==original.tobytes()
        assert not camera.pending_acquisition_payloads
        assert not list(tmp_path.glob('*.h5'))
        if defect=='stop':assert 'SDK read timed out' in str(error.value) and 'SDK stop also failed' in str(error.value)
    finally:fake.stop=original_stop;camera.close()


def test_continuous_stop_preserves_tail_without_another_setpoint(camera,tmp_path):
    pixels,metadata=batch(90,3)
    def frames(*args,**kwargs):yield pixels,metadata
    camera.iter_frames=frames
    manager=CameraScanManager(camera,tmp_path);stop=threading.Event();starts=[]
    scan=manager.execute_scan('continuous','delay',0,1,3,1,continuous=True,abort_check=stop.is_set,on_step_start=lambda step,value:starts.append(step))
    next(scan);stop.set();assert list(scan)==[]
    assert starts==[0] and len(list(tmp_path.glob('*.h5')))==1
    assert_all_frames_recoverable(tmp_path,{90+i:frame for i,frame in enumerate(pixels)})
    assert sum(context['frames'] for _,_,context in recovered_records(tmp_path))==2
    assert not any(thread.name=='camera-sdk-reader' for thread in threading.enumerate())


def test_recovery_write_failure_retains_original_manager_ownership_for_retry(camera,tmp_path,monkeypatch):
    pixels,metadata=batch(100,2)
    def frames(*args,**kwargs):
        yield pixels,metadata
        raise RuntimeError('original acquisition failure')
    camera.iter_frames=frames
    original=RecoveryJournal._write_payload
    def fail(*args):raise OSError('recovery volume unavailable')
    monkeypatch.setattr(RecoveryJournal,'_write_payload',fail)
    manager=CameraScanManager(camera,tmp_path)
    with pytest.raises(RuntimeError,match='recovery volume unavailable') as error:
        list(manager.execute_scan('retry','delay',0,1,1,2))
    assert 'original acquisition failure' in str(error.value)
    assert manager.pending_recovery_payloads
    with pytest.raises(RuntimeError,match='Unpersisted acquisition evidence'):manager.start_live()
    monkeypatch.setattr(RecoveryJournal,'_write_payload',original)
    manager.retry_pending_recovery()
    assert not manager.pending_recovery_payloads
    assert_all_frames_recoverable(tmp_path,{100:pixels[0],101:pixels[1]})


@pytest.mark.parametrize('buffered',[False,True])
@pytest.mark.parametrize('defect',['dtype','shape','metadata_count'])
def test_malformed_batch_remains_separate_and_unchanged(camera,tmp_path,buffered,defect):
    pixels,metadata=batch(110,2)
    if defect=='dtype':pixels=pixels.astype(np.int32);pixels[0,0,:2]=[70000,-1]
    if defect=='shape':pixels=pixels[:,:3]
    if defect=='metadata_count':metadata=metadata[:1]
    original_pixels=pixels.copy();original_metadata=json.loads(json.dumps(metadata))
    def frames(*args,**kwargs):yield pixels,metadata
    camera.iter_frames=frames
    source=pipeline.BufferedCamera(camera,2,4,1024*1024,0,lambda:None) if buffered else None
    manager=CameraScanManager(source or camera,tmp_path)
    kwargs=dict(run_id='bad',batch_size=4,queue_batches=1,abort_check=None)
    if source is not None:kwargs['source']=source
    try:
        with pytest.raises((ValueError,RuntimeError)):manager._acquire_and_save_step('bad',0,'delay',0,2,**kwargs)
        records=recovered_records(tmp_path)
        assert len(records)==1
        assert records[0][0].dtype==original_pixels.dtype and records[0][0].shape==original_pixels.shape
        assert records[0][0].tobytes()==original_pixels.tobytes()
        assert records[0][1]==original_metadata
        assert not list(tmp_path.glob('*.h5'))
    finally:
        if source is not None:source.close()


def test_queue_full_retains_unsubmitted_batch_and_joins_cooperatively(camera,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    entered,release,closed=threading.Event(),threading.Event(),threading.Event()
    returned={}
    class Unpausable(TinyCamera):
        @property
        def can_pause_acquisition(self):return False
    camera=Unpausable();camera.connect()
    def frames(*args,**kwargs):
        try:
            for identifier in range(120,128):
                pixels,metadata=batch(identifier,1);returned[identifier]=pixels[0].copy()
                yield pixels,metadata
        finally:closed.set()
    camera.iter_frames=frames
    manager=CameraScanManager(camera,tmp_path);original=manager._write_batch
    def write(*args):
        entered.set();assert release.wait(5);original(*args)
    monkeypatch.setattr(manager,'_write_batch',write)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(manager.acquire_and_save_step,'backpressure',0,'delay',0,8,batch_size=1,queue_batches=1)
        try:
            assert entered.wait(3) and closed.wait(3)
            release.set()
            with pytest.raises(AcquisitionBackpressure):future.result(timeout=5)
            assert_all_frames_recoverable(tmp_path,returned)
            assert len(returned)==3
        finally:release.set();camera.close()



def test_failed_final_hdf5_flush_never_marks_partial_complete(camera,tmp_path,monkeypatch):
    original=h5py.File.flush
    failed=[]
    def flush(h5):
        if h5.attrs.get('complete',False) and not failed:
            failed.append(True);raise OSError('final flush unavailable')
        return original(h5)
    pixels,metadata=batch(140,2)
    def frames(*args,**kwargs):yield pixels,metadata
    camera.iter_frames=frames
    monkeypatch.setattr(h5py.File,'flush',flush)
    manager=CameraScanManager(camera,tmp_path)
    with pytest.raises(RuntimeError,match='final flush unavailable'):
        manager.acquire_and_save_step('flush',0,'delay',0,2)
    with h5py.File(filesystem_path(next(tmp_path.glob('*.h5.partial')))) as h5:
        assert not h5.attrs['complete'] and h5.attrs['frames_written']==2
    assert_all_frames_recoverable(tmp_path,{140:pixels[0],141:pixels[1]})


@pytest.mark.parametrize('instrumentation',['profile','hash'])
def test_instrumentation_failure_before_handoff_preserves_payload(camera,tmp_path,monkeypatch,instrumentation):
    from scripts import capture_diagnostics,storage_diagnostics
    pixels,metadata=batch(150,2)
    def frames(*args,**kwargs):yield pixels,metadata
    camera.iter_frames=frames
    def fail(*args):raise RuntimeError('instrumentation failed')
    if instrumentation=='profile':
        probe=SimpleNamespace(stage=lambda *_:None,observe=fail)
        source=capture_diagnostics.ProfileCamera(camera,probe)
    else:
        source=storage_diagnostics._PixelReferenceCamera(camera)
        monkeypatch.setattr(storage_diagnostics,'hashlib',SimpleNamespace(sha256=fail))
    manager=CameraScanManager(source,tmp_path)
    with pytest.raises(RuntimeError,match='instrumentation failed'):
        manager.acquire_and_save_step('instrumentation',0,'delay',0,2)
    assert_all_frames_recoverable(tmp_path,{150:pixels[0],151:pixels[1]})
