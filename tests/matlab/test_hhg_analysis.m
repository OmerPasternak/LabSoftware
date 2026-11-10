function test_hhg_analysis(root,out)
%TEST_HHG_ANALYSIS Offline synthetic integration and source-equation checks.
addpath(fullfile(root,'src','HHG analysis','matlab'));
set(groot,'DefaultFigureVisible','off');
cleanup=onCleanup(@()close('all','force')); %#ok<NASGU>
fig=hhg_analysis_start();
assert(contains(which('ShowScanGUI'),[filesep 'active' filesep]));
assert(isequal(hhg_analysis_start(),fig));
controls=guidata(fig);
assert(strcmp(get(controls.pushbutton1,'String'),'Load Scan ...'));
assert(strcmp(get(controls.pushbutton22,'String'),'Load Two Color Scan ...'));
assert(isa(get(controls.pushbutton2,'Callback'),'function_handle'));
assert(strcmp(get(controls.pushbutton18,'Enable'),'off'));
set(fig,'Units','pixels'); bounds=get(fig,'Position'); screen=get(groot,'ScreenSize');
assert(all(bounds(3:4)<=screen(3:4)));
assert(strcmp(get(controls.axes1,'Units'),'normalized'));
reference=openfig(fullfile(root,'src','HHG analysis','matlab','reference','ShowScanGUI.fig'),'invisible');
buttons=findall(reference,'Style','pushbutton');
for k=1:numel(buttons)
    active=findall(fig,'Tag',get(buttons(k),'Tag'));
    assert(isequal(get(active,'String'),get(buttons(k),'String')),'Original button label changed.');
end

assert(isequal(colormap(reference),colormap(fig))); close(reference);
hhg_analysis_load_hdf5(fig,fullfile(out,'synthetic.h5'));
h=guidata(fig); assert(isempty(h.data)); original=hhg_analysis_read(fig,1,h.flength);
assert(isequal(size(original),[10 6 263])); frameCount=size(original,3);
assert(isa(original,'uint16'));
hhg_analysis_export_workspace(fig);
assert(isequal(evalin('base','data'),original));
assert(isequal(evalin('base','delay'),squeeze(h.delay)));
assert(~isempty(which('CovarianceFromGUIdata'))&&~isempty(which('TConlyAnalysisFromGUIdata')));


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
hhg_analysis_export_workspace(fig);
assert(isequal(evalin('base','roidata'),squeeze(roi)));
assert(isequal(evalin('base','roidataint'),squeeze(sum(sum(roi,1),2))));
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
hhg_analysis_load_hdf5(fig,fullfile(out,'synthetic.h5'),'frames',3);
hhg_analysis_export_workspace(fig);
assert(isequal(evalin('base','data'),original(:,:,3)));
assert(isvector(evalin('base','lineout')));
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
