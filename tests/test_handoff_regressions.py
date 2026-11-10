"""Tiny independent regressions for the final review; never use physical hardware."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from hhg_control.camera.analysis import correlation, hdf5_io, spectral, harmonic_statistics
from hhg_control.camera.analysis.beam_profiling import compute_beam_centroid, fit_gaussian_beam_profile
from hhg_control.safe_io import filesystem_path


def measurement(path, data=None, **attrs):
    if data is None: data=np.arange(4*2*8,dtype=np.uint16).reshape(4,2,8)+10
    with h5py.File(filesystem_path(path),'w') as h5:
        h5.create_dataset('images',data=data)
        h5.attrs.update(schema_version='2.0',complete=True,frames_written=len(data),num_frames=len(data),
                        run_id='run',scan_step_index=0,scan_parameter_name='delay',scan_parameter_units='fs',
                        scan_parameter_value=0.,camera_serial='test',camera_model='test',exposure_time_s=.01,
                        trigger_mode='auto sequence',readout_mode='ROLLING_SHUTTER',roi_bounds=[0,0,8,2])
        h5.attrs.update(attrs)
    return path


@pytest.mark.parametrize('field,value',[('run_id','other'),('complete',False),('frames_written',3),('roi_bounds',[1,0,9,2]),('exposure_time_s',.02),('trigger_mode','external exposure start')])
def test_aggregation_rejects_unrelated_or_incomplete_inputs(tmp_path,field,value):
    a=measurement(tmp_path/'a.h5'); b=measurement(tmp_path/'b.h5',scan_step_index=1,**{field:value})
    hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)]
    with pytest.raises(ValueError): hdf5_io.load_full_scan(tmp_path)
    assert hashes==[hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)]


def test_explicit_repeats_and_pooling_are_separate_decisions(tmp_path):
    measurement(tmp_path/'a.h5'); measurement(tmp_path/'b.h5')
    with pytest.raises(ValueError,match='Duplicate'): hdf5_io.load_full_scan(tmp_path)
    assert hdf5_io.load_full_scan(tmp_path,repeat_policy='allow')['step_indices']==[0,0]
    measurement(tmp_path/'b.h5',scan_step_index=1,scan_parameter_value=1.)
    with pytest.raises(ValueError,match='Pooling'): correlation.compute_g2_from_scan(tmp_path)
    actual,_=correlation.compute_g2_from_scan(tmp_path,pool_steps=True)
    data=np.arange(4*2*8,dtype=np.float64).reshape(4,2,8)+10
    np.testing.assert_allclose(actual,np.mean(data**2,axis=0)/np.mean(data,axis=0)**2)


@pytest.mark.parametrize('batch',[1,3])
def test_harmonic_ci_shares_two_passes_and_matches_independent_oracle(tmp_path,monkeypatch,batch):
    data=np.arange(5*2*8,dtype=np.uint16).reshape(5,2,8)+1
    path=measurement(tmp_path/'data.h5',data)
    regions=[dict(label='H11',order=11,x_start=0,x_stop=2),dict(label='H13',order=13,x_start=4,x_stop=8)]
    map_path=spectral.save_harmonic_regions(tmp_path/'map.json',regions,path,(2,8),roi_bounds=[0,0,8,2])
    original=spectral.iter_scan_step_frames; passes=[]
    def counted(*args,**kwargs):
        passes.append(1); yield from original(*args,**kwargs)
    monkeypatch.setattr(spectral,'iter_scan_step_frames',counted)
    result=spectral.compute_harmonic_g2_from_scan(path,map_path,batch_size=batch)
    assert len(passes)==2
    for row,region in zip(result['harmonics'],regions):
        signal=data[:,:,region['x_start']:region['x_stop']].sum(axis=(1,2),dtype=float)
        mu=np.mean(signal); q=np.mean(signal**2)
        influence=(signal**2-q)/mu**2-2*q*(signal-mu)/mu**3
        assert row['g2_zero_delay']==pytest.approx(q/mu**2)
        assert row['standard_error']==pytest.approx(np.std(influence,ddof=1)/np.sqrt(len(signal)))


def test_acquisition_g2_streams_one_iterator_and_names_raw_and_corrected_means():
    data=np.array([[[1,9]],[[3,5]],[[7,2]]],dtype=np.uint16); calls=[]
    def iterator(*args,**kwargs):
        calls.append(args)
        for frame in data: yield frame[None],[{}]
    camera=SimpleNamespace(iter_frames=iterator,get_exposure_time=lambda:.01)
    actual,stats=correlation.acquire_and_compute_g2(camera,3,background=2)
    corrected=np.maximum(data.astype(float)-2,0)
    np.testing.assert_allclose(actual,np.mean(corrected**2,axis=0)/np.mean(corrected,axis=0)**2)
    np.testing.assert_allclose(stats['raw_mean_intensity_map'],data.mean(axis=0))
    np.testing.assert_allclose(stats['corrected_mean_intensity_map'],corrected.mean(axis=0))
    assert len(calls)==1


@pytest.mark.parametrize('shape,center', [((16,64),(12,5)),((9,13),(2,7)),((3,4),(1,1))])
def test_cropped_beam_moments_use_one_explicit_background(shape,center):
    y,x=np.indices(shape); signal=np.exp(-((x-center[0])**2/18+(y-center[1])**2/8))
    image=signal+7; fit=fit_gaussian_beam_profile(image,background=7)
    mu_x=(signal*x).sum()/signal.sum();mu_y=(signal*y).sum()/signal.sum()
    np.testing.assert_allclose(compute_beam_centroid(image,7),(mu_x,mu_y),atol=1e-12)
    assert fit['sigma_x']==pytest.approx(np.sqrt((signal*(x-mu_x)**2).sum()/signal.sum()),abs=.0051)
    assert fit['sigma_y']==pytest.approx(np.sqrt((signal*(y-mu_y)**2).sum()/signal.sum()),abs=.0051)


@pytest.mark.parametrize('level',[0,100])
def test_blank_beam_is_undefined_instead_of_a_fabricated_center(level):
    image=np.full((16,64),level)
    with pytest.raises(ValueError,match='undefined'): compute_beam_centroid(image)
    with pytest.raises(ValueError,match='undefined'): fit_gaussian_beam_profile(image)


@pytest.mark.parametrize('field,value',[('scan_parameter_value',999.),('trigger_mode','external exposure start'),('scan_parameter_units','mm')])
def test_si_pairs_normal_acquisition_roi_and_rejects_coordinate_mismatch_before_moments(tmp_path,monkeypatch,field,value):
    paths=[measurement(tmp_path/f'{label}.h5') for label in ('on','off','bg')]
    regions=[dict(label='H11',order=11,x_start=0,x_stop=8)]
    map_path=spectral.save_harmonic_regions(tmp_path/'map.json',regions,paths[0],(2,8),roi_bounds=[0,0,8,2])
    matched=harmonic_statistics.compute_si_harmonic_g2(*paths,map_path)
    assert matched['pairing_policy'] and len(matched['harmonics'])==1
    with h5py.File(paths[1],'r+') as h5:h5.attrs[field]=value
    monkeypatch.setattr(harmonic_statistics,'_moments',lambda *_:pytest.fail('moments before pairing validation'))
    with pytest.raises(ValueError,match='ON/OFF reference mismatch'):
        harmonic_statistics.compute_si_harmonic_g2(*paths,map_path)


