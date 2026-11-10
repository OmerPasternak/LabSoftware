function test_hhg_analysis(root,out)
%TEST_HHG_ANALYSIS Offline synthetic integration and source-equation checks.
addpath(fullfile(root,'src','HHG analysis','matlab'));
set(groot,'DefaultFigureVisible','off');
cleanup=onCleanup(@()close('all','force')); %#ok<NASGU>
fig=hhg_analysis_start();
assert(contains(which('ShowScanGUI'),[filesep 'active' filesep]));
assert(isequal(hhg_analysis_start(),fig));
controls=guidata(fig);
assert(contains(get(controls.pushbutton1,'String'),'HDF5'));
assert(contains(get(controls.pushbutton22,'String'),'HDF5'));
assert(isa(get(controls.pushbutton2,'Callback'),'function_handle'));
assert(strcmp(get(controls.pushbutton18,'Enable'),'off'));
set(fig,'Units','pixels'); bounds=get(fig,'Position'); screen=get(groot,'ScreenSize');
assert(all(bounds(3:4)<=screen(3:4)));
assert(strcmp(get(controls.axes1,'Units'),'normalized'));
reference=openfig(fullfile(root,'src','HHG analysis','matlab','reference','ShowScanGUI.fig'),'invisible');
assert(isequal(colormap(reference),colormap(fig))); close(reference);
hhg_analysis_load_hdf5(fig,fullfile(out,'synthetic.h5'));
h=guidata(fig); assert(isempty(h.data)); original=hhg_analysis_read(fig,1,h.flength);
assert(isequal(size(original),[10 6 263])); frameCount=size(original,3);
assert(isa(original,'uint16'));
assert(isequal(original,h5read(fullfile(out,'synthetic.h5'),'/images')));
opts=hhg_analysis_configure(fig,false); opts.rows=[2 4]; opts.columns=[3 8]; opts.slice=4;
dataNow=permute(original,[2 1 3]);
spectra=squeeze(mean(dataNow(2:4,:,:),1))';
expectedCov=cov(spectra); expectedPearson=corrcoef(spectra);
r=hhg_analysis_workflow(fig,'covariance',opts,false);
cache=getappdata(fig,'hhg_reduced'); assert(cache.matrix_updates==2&&cache.count==frameCount);
assert(max(abs(r.values-expectedCov),[],'all')<1e-10);
assert(max(abs(cov(spectra*3)-r.values*9),[],'all')<1e-8);
r=hhg_analysis_workflow(fig,'pearson',opts,false);
assert(max(abs(r.values-expectedPearson),[],'all')<1e-10);
assert(isequal(isnan(r.values),isnan(expectedPearson)));
r=hhg_analysis_workflow(fig,'covariance_diagonal',opts,false);
assert(max(abs(r.values-diag(expectedCov)))<1e-9);
r=hhg_analysis_workflow(fig,'pearson_slice',opts,false);
assert(max(abs(r.values-expectedPearson(4,:)))<1e-10);
r=hhg_analysis_workflow(fig,'repeats',opts,false);
assert(isequal(r.values,spectra'));
r=hhg_analysis_workflow(fig,'variance_image',opts,false);
assert(max(abs(r.values-squeeze(var(single(dataNow),[],3))),[],'all')<1e-3);
r=hhg_analysis_workflow(fig,'variance_line',opts,false);
assert(max(abs(r.values-var(spectra,0,1)),[],'all')<1e-10);
r=hhg_analysis_workflow(fig,'legacy_space',opts,false);
assert(isequaln(r.values,fliplr(corrcoef(squeeze(mean(dataNow(:,3:8,:),1))'))));
r=hhg_analysis_workflow(fig,'single_image',opts,false);
assert(isequal(r.values,flipud(fliplr(original(:,:,1)'))));
% Compare plotting conventions with executable snippets from the source.
r=hhg_analysis_workflow(fig,'dc',opts,false); hhg_analysis_plot(fig,r);
actual=gcf; ax=findobj(actual,'Type','axes'); actualDirection=get(ax(1),'YDir');
ref=figure; hold on; imagesc(squeeze(mean(dataNow,3)));
assert(strcmp(actualDirection,get(gca,'YDir')));
assert(isequal(colormap(actual),colormap(ref))); close(actual); close(ref);
hhg_analysis_select_roi(fig,[2 2 3 2]); h=guidata(fig);
assert(h.roiwidth==3&&h.roiheight==2);
flipData=flip(original,2); roi=flipData(2:4,2:3,:);
hhg_analysis_trace(fig);
trace=get(findall(h.axes3,'Type','line'),'YData');
assert(isequal(trace(:),reshape(sum(sum(roi,1),2),[],1)));
assert(isempty(h.data)); assert(isequal(hhg_analysis_read(fig,1,h.flength),original));
% Explicitly verified TC axis, plus unchanged uint16 subtraction semantics.
hhg_analysis_set_tc_axis(fig,26.7+(0:frameCount-1)*0.001);
opts.tc_subtract=false; r=hhg_analysis_workflow(fig,'tc',opts,false);
assert(max(abs(r.delay_fs-(1:frameCount)/(5.8*4)*2.67))<1e-12);
refPixels=original(:,:,1); setappdata(fig,'hhg_tc_reference',struct('pixels',refPixels,'filename','synthetic reference'));
opts.tc_subtract=true; r=hhg_analysis_workflow(fig,'tc',opts,false);
subtracted=original-refPixels;
assert(isequal(r.values,reshape(mean(subtracted(:,2:4,:),2),10,frameCount)));
% Per-pixel transform keeps opposite phases separate; no hidden aggregation.
n=16; wave=cos(2*pi*3*(0:n-1)/n);
pixels=zeros(2,2,n); pixels(1,1,:)=wave; pixels(2,1,:)=-wave;
fftResult=hhg_analysis_pixel_fft(pixels,0:n-1,'synthetic',false);
assert(abs(fftResult.magnitude(1,1,4)-8)<1e-10);
assert(abs(fftResult.magnitude(2,1,4)-8)<1e-10);
assert(contains(fftResult.aggregation,'INACTIVE'));
hhg_analysis_pixel_fft(pixels,0:n-1,'synthetic rendered',true);
[coeff,score,latent]=hhg_analysis_pca(spectra);
assert(max(abs(score*coeff'-(spectra-mean(spectra,1))),[],'all')<1e-10);
assert(max(abs(sort(eig(cov(spectra)),'descend')-[latent;zeros(size(spectra,2)-numel(latent),1)]))<1e-8);
r=hhg_analysis_workflow(fig,'pca_space',opts,false); assert(isfield(r,'explained_fraction'));
r=hhg_analysis_workflow(fig,'pca_frequency',opts,false); assert(isfield(r,'pca_method'));
set(h.edit1,'String','1');
pixelResult=hhg_analysis_current_fourier(fig,'full',false);
direct=abs(fft(double(original),[],3));
assert(max(abs(pixelResult.magnitude-direct(:,:,1:floor(frameCount/2)+1)),[],'all')<1e-8);
maskResult=hhg_analysis_current_masks(fig,'pushbutton25',fullfile(out,'harmonics.msk'),false);
assert(isequal(maskResult.traces(:,1),reshape(sum(sum(roi,1),2),[],1)));
hhg_analysis_export_mat(fig,fullfile(out,'raw_export.mat'));
saved=load(fullfile(out,'raw_export.mat')); assert(saved.complete&&isequal(saved.data,original));
% Diagonal-only requests do not allocate a quadratic covariance or Pearson.
hhg_analysis_invalidate_derived(fig);
r=hhg_analysis_workflow(fig,'covariance_diagonal',opts,false);
cache=getappdata(fig,'hhg_reduced'); assert(~isfield(cache,'covariance')&&~isfield(cache,'pearson'));
assert(max(abs(r.values-diag(expectedCov)))<1e-9);
hhg_analysis_invalidate_derived(fig);
r=hhg_analysis_workflow(fig,'pearson_slice',opts,false);
cache=getappdata(fig,'hhg_reduced'); assert(~isfield(cache,'covariance')&&isfield(cache,'covariance_slice'));
assert(max(abs(r.values-expectedPearson(4,:)))<1e-10);
cache.spectra=[]; setappdata(fig,'hhg_reduced',cache);
r=hhg_analysis_workflow(fig,'pca_space',opts,false); assert(~isempty(r.values));
expect_error(@()hhg_analysis_workflow(fig,'pca_frequency',opts,false),'HHG:Memory');
% Replacing an enabled reference invalidates all derived caches, preserving axis.
h=guidata(fig); h.nsel=1; guidata(fig,h);
hhg_analysis_current_action(fig,h.pushbutton19,[]); setappdata(fig,'hhg_apply_background',true);
setappdata(fig,'hhg_xW',1:10); setappdata(fig,'hhg_axis_source','synthetic calibration');
hhg_analysis_workflow(fig,'covariance',opts,false); hhg_analysis_workflow(fig,'dc',opts,false);
h=guidata(fig); h.nsel=2; guidata(fig,h);
hhg_analysis_current_action(fig,h.pushbutton19,[]);
assert(~isappdata(fig,'hhg_reduced')&&~isappdata(fig,'hhg_image_reduced')&&~isappdata(fig,'hhg_last_result'));
assert(isequal(getappdata(fig,'hhg_xW'),1:10));
corrected=original-original(:,:,2)*.95;
r=hhg_analysis_workflow(fig,'dc',opts,false);
assert(max(abs(r.values-mean(corrected,3)'),[],'all')<1e-10);
r=hhg_analysis_workflow(fig,'covariance',opts,false);
expectedCorrected=reshape(mean(corrected(:,2:4,:),2),10,frameCount)';
assert(max(abs(r.values-cov(expectedCorrected)),[],'all')<1e-9);
hhg_analysis_export_mat(fig,fullfile(out,'corrected_export.mat'));
correctedSaved=load(fullfile(out,'corrected_export.mat'));
maskCorrected=hhg_analysis_current_masks(fig,'pushbutton25',fullfile(out,'harmonics.msk'),false);
assert(maskCorrected.processing.background_enabled);
fftCorrected=hhg_analysis_current_fourier(fig,'full',false);
assert(fftCorrected.processing.background_enabled&&fftCorrected.processing.bin_size==1);
provenanceCsv=hhg_analysis_write_csv(fullfile(out,'corrected_trace.csv'),[1 2;3 4],maskCorrected.processing);
provenance=jsondecode(fileread([provenanceCsv '.processing.json'])); assert(provenance.background_enabled);
assert(correctedSaved.processing.background_enabled&&correctedSaved.processing.background.scale==.95);
assert(isequal(correctedSaved.data,corrected)&&~saved.processing.background_enabled);
% Failed source loading leaves the entire prior source/navigation/context intact.
priorH=guidata(fig); priorSource=getappdata(fig,'hhg_hdf5_source'); priorCache=getappdata(fig,'hhg_reduced');
priorLabel=get(priorH.text2,'String'); priorSlider=get(priorH.slider1,'Max');
expect_error(@()hhg_analysis_load_hdf5(fig,fullfile(out,'other_exposure.h5')),'HHG:Reference');
assert(isequal(guidata(fig),priorH)&&isequal(getappdata(fig,'hhg_hdf5_source'),priorSource));
assert(isequal(getappdata(fig,'hhg_reduced'),priorCache)&&isequal(getappdata(fig,'hhg_xW'),1:10));
assert(isequal(get(priorH.text2,'String'),priorLabel)&&get(priorH.slider1,'Max')==priorSlider);
setappdata(fig,'hhg_apply_background',false);
% A failed harmonic FFT export leaves the complete prior workspace bundle.
hhg_analysis_load_hdf5(fig,{fullfile(out,'nonuniform0.h5'),fullfile(out,'nonuniform1.h5'),fullfile(out,'nonuniform2.h5')},'step_means');
h=guidata(fig); set(h.edit2,'String','transaction');
assignin('base','transaction_H13data',123); assignin('base','transaction_piezoaxis',456);
assignin('base','transaction_filename','prior'); assignin('base','transaction_processing','prior');
expect_error(@()hhg_analysis_current_masks(fig,'pushbutton27',fullfile(out,'harmonics.msk'),false),'HHG:FFT');
assert(evalin('base','transaction_H13data')==123&&evalin('base','transaction_piezoaxis')==456);
assert(strcmp(evalin('base','transaction_filename'),'prior')&&strcmp(evalin('base','transaction_processing'),'prior'));
% Legacy complete input preserves arithmetic/orientation; truncated reads fail/close.
handlesBefore=openedFiles();
for count=[0 1 6 15]
    expect_error(@()hhg_analysis_read_legacy_bin(fullfile(out,sprintf('legacy_%d.bin',count)),4,4),'HHG:LegacyBIN');
    assert(isequal(openedFiles(),handlesBefore));
end
legacy=hhg_analysis_read_legacy_bin(fullfile(out,'legacy_16.bin'),4,4);
assert(isequal(legacy,reshape(uint16(0:15),4,4)));
hhg_analysis_load_hdf5(fig,{fullfile(out,'step1.h5'),fullfile(out,'step0.h5')},'step_means');
scanSource=getappdata(fig,'hhg_hdf5_source'); assert(isequal(scanSource.step_indices,[0 1]));
mean0=mean(h5read(fullfile(out,'step0.h5'),'/images'),3);
mean1=mean(h5read(fullfile(out,'step1.h5'),'/images'),3);
assert(isequal(hhg_analysis_read(fig,1,2),cat(3,mean0,mean1)));
expect_error(@()hhg_analysis_load_hdf5(fig,{fullfile(out,'step0.h5'),fullfile(out,'wrong_camera.h5')},'step_means'),'HHG:Metadata');
colors=hhg_analysis_devon(); assert(isequal(size(colors),[256 3]));
csv=hhg_analysis_write_csv(fullfile(out,'derived.csv'),[1 2;3 4]);
csv2=hhg_analysis_write_csv(fullfile(out,'derived.csv'),[9 8;7 6]);
assert(~strcmp(csv,csv2)); assert(isequal(readmatrix(csv),[1 2;3 4]));
expect_error(@()hhg_analysis_pixel_fft(pixels,[0:14 17],'bad',false),'HHG:FFT');
expect_error(@()hhg_analysis_load_hdf5(fig,fullfile(out,'incomplete.h5')),'HHG:Incomplete');
expect_error(@()hhg_analysis_load_hdf5(fig,fullfile(out,'count_mismatch.h5')),'HHG:Incomplete');
expect_error(@()hhg_analysis_load_hdf5(fig,fullfile(out,'synthetic.h5.partial')),'HHG:Incomplete');
hhg_analysis_load_hdf5(fig,fullfile(out,'single.h5'));
h=guidata(fig); assert(h.flength==1&&strcmp(get(h.slider1,'Enable'),'off'));
assert(~isappdata(fig,'hhg_tc_confirmed'));
assert(~isappdata(fig,'hhg_xW'));
assert(~isappdata(fig,'hhg_options'));
assert(isequal(hhg_analysis_limits(1),[.5 1.5]));
exportapp(fig,fullfile(out,'matlab_analysis_gui.png'));
disp('HHG_ANALYSIS_SYNTHETIC_CHECKS_PASSED');
end

function expect_error(callback,id)
try
    callback();
catch e
    assert(strcmp(e.identifier,id),'Unexpected error: %s',getReport(e)); return;
end
error('Expected error %s',id);
end
