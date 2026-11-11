function result = hhg_analysis_workflow(fig,action,opts,makePlots)
%HHG_ANALYSIS_WORKFLOW Parameterized calculations from the supplied lab scripts.
% data is [spectral,spatial,frame], as exported by ShowScanGUI. Pixel windows
% are one-based inclusive. Spectral statistics average spatial pixels before
% taking centered sample covariance (N-1). This differs from Python's y SUM.
% Raw arrays/files are never changed. Missing scientific helpers fail closed.
if nargin<4, makePlots=true; end
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load an image or scan first.');
if strcmp(action,'grating_calibration'), require({'findpeaks','fit','fittype','fitoptions'}); end
if nargin<3
    if ~isappdata(fig,'hhg_options')
        [~,accepted]=hhg_analysis_configure(fig);
        if ~accepted, result=[]; return; end
    end
    opts=hhg_analysis_configure(fig,false);
end
hhg_analysis_validate_options(opts,h);
if isappdata(fig,'hhg_hdf5_source')
    result=hhg_analysis_current_workflow(fig,action,opts,makePlots); return;
end
rows=opts.rows(1):opts.rows(2); cols=opts.columns(1):opts.columns(2);
xW=getappdata(fig,'hhg_xW');
result=struct('action',action,'source',h.filename,'settings',opts, ...
    'frame_count',h.flength,'data_dimensions',size(h.data),'xW',xW, ...
    'axis_source',getappdata(fig,'hhg_axis_source'),'created_utc', ...
    char(datetime('now','TimeZone','UTC','Format','yyyy-MM-dd''T''HH:mm:ssXXX')));
statistics={'pearson','covariance','flipped_pearson','calibrated_pearson', ...
    'covariance_diagonal','pearson_slice','variance_line','pca_frequency'};
if ismember(action,statistics)
    assert(h.flength>=2,'HHG:Frames','At least two frames are needed for sample statistics.');
    spectra=reshape(mean(h.data(:,rows,:),2),h.width,h.flength)';
    result.mean_spectrum=mean(spectra,1);
    result.reduction='mean over selected spatial rows before moments';
end
matrixActions={'pearson','covariance','flipped_pearson','calibrated_pearson','covariance_diagonal','pearson_slice'};
if ismember(action,matrixActions)
    assert(4*8*double(h.width)^2<=1024^3,'HHG:Memory','Covariance workspace exceeds 1 GiB. Choose the Python streaming tool or a smaller explicit spectral ROI.');
end
switch action
    case 'dc'
        result.values=squeeze(mean(permute(h.data,[2 1 3]),3));
    case {'pearson','flipped_pearson','calibrated_pearson','pearson_slice'}
        result.values=corrcoef(spectra);
        result.estimator='corrcoef of spatial MEANS across frames; constant channels undefined';
        if strcmp(action,'pearson_slice'), result.values=result.values(opts.slice,:); end
        if strcmp(action,'calibrated_pearson')
            assert(~isempty(xW),'HHG:Calibration','Import a validated xW axis first. Automatic calibration is missing its helper.');
        end
    case {'covariance','covariance_diagonal'}
        result.values=cov(spectra);
        result.estimator='centered sample covariance, N-1, of spatial MEANS';
        if strcmp(action,'covariance_diagonal'), result.values=diag(result.values); end
    case 'repeats'
        result.values=reshape(mean(h.data(:,rows,:),2),h.width,h.flength);
    case 'variance_image'
        assert(h.flength>=2,'HHG:Frames','Variance requires two or more frames.');
        result.values=squeeze(var(single(permute(h.data,[2 1 3])),[],3));
        result.estimator='var(single(dataNow),[],3), N-1';
    case 'variance_line'
        result.values=var(spectra,0,1);
    case 'legacy_space'
        assert(h.flength>=2,'HHG:Frames','Correlation requires two or more frames.');
        % The source averages dimension 1 of dataNow (spatial), despite the
        % "space cov" heading. Retain this exact calculation and label it.
        subset=reshape(mean(h.data(cols,:,:),2),numel(cols),h.flength)';
        result.values=fliplr(corrcoef(subset));
        result.estimator='legacy space-cov block: spectral subset, spatial MEAN, horizontal flip';
    case {'pca_space','pca_frequency'}
        assert(h.flength>=2,'HHG:Frames','PCA requires two or more frames.');
        if strcmp(action,'pca_space')
            observations=reshape(mean(h.data(cols,:,:),1),h.height,h.flength)';
        else
            observations=spectra;
        end
        [result.coefficients,result.scores,result.latent,result.pca_method]=hhg_analysis_pca(observations);
        result.explained_fraction=result.latent/sum(result.latent);
        result.values=result.coefficients;
    case 'single_image'
        result.values=flipud(fliplr(h.data(:,:,h.nsel)'));
        result.selected_frame=h.nsel;
    case {'tc','tc_fft'}
        if strcmp(action,'tc_fft')
            require({'fftPerCell'});
        end
        steps=[0.001 0.002 0.003 0.005 0.010 0.050];
        assert(any(abs(opts.tc_step-steps)<1e-9),'HHG:TC','The original empirical TC calibration only specifies steps 0.001, 0.002, 0.003, 0.005, 0.010 and 0.050 mm.');
        assert(h.flength>=2,'HHG:Frames','TC requires at least two delays.');
        unit=getappdata(fig,'hhg_delay_label');
        assert(strcmp(unit,'TC position (mm)')||isappdata(fig,'hhg_tc_confirmed'), ...
            'HHG:TCAxis','TC needs mechanical delays in mm. HDF5 frame indices are not delay positions. Use a .tcs scan or assign a verified axis through hhg_analysis_set_tc_axis.');
        assert(all(abs(diff(h.delay)-opts.tc_step)<1e-7),'HHG:TCAxis','Loaded TC delays are not uniformly spaced at the configured step.');
        working=h.data;
        if opts.tc_subtract
            assert(isappdata(fig,'hhg_tc_reference'),'HHG:Reference','Capture a coherent-only frame before loading the TC scan.');
            reference=getappdata(fig,'hhg_tc_reference');
            assert(isequal(size(reference.pixels),[h.width h.height]),'HHG:Reference','Coherent-only reference geometry differs from the scan.');
            working=working-reference.pixels;
            result.reference=rmfield(reference,'pixels');
            result.subtraction_class=class(working);
            result.subtraction='original MATLAB subtraction semantics; uint16 inputs saturate at zero';
        end
        result.values=reshape(mean(working(:,rows,:),2),h.width,h.flength);
        stepToWave=opts.tc_step/(0.001*5.8*4);
        result.delay_fs=(1:h.flength)*stepToWave*2.67;
        result.delay_mm=h.delay;
        result.empirical_calibration='1600 nm historical calibration: step/(0.001*5.8*4), multiplied by 2.67 fs';
        if strcmp(action,'tc_fft')
            dataNow=permute(working,[2 1 3]);
            absFT=abs(fftPerCell(squeeze(dataNow(rows,:,:))));
            result.values=squeeze(mean(absFT,1));
            result.values=result.values./result.values(:,1);
            L2=h.flength-1; Fs=1/stepToWave;
            result.frequencies=Fs*(0:floor(L2/2))/L2;
            assert(size(result.values,2)==numel(result.frequencies),'HHG:FFT','Restored fftPerCell output does not match the source frequency-axis convention.');
        end
    case 'calibration'
        require({'wAxis_fromLocs','findpeaks'});
        assert(h.width==2500&&h.height>=100,'HHG:Calibration','The supplied FWHM routine requires 2500 spectral pixels and spatial rows 40:100.');
        answers=inputdlg({'Harmonic orders (matched to descending detected pixel locations)'},'FWHM calibration',1,{'11 13 15 17'});
        if isempty(answers), return; end
        orders=sscanf(answers{1},'%f')';
        assert(numel(orders)>=3&&all(isfinite(orders))&&all(diff(orders)>0),'HHG:Calibration','Enter at least three increasing finite harmonic orders.');
        result.values=wAxis_Calibration(h.data,true,orders,numel(orders));
        setappdata(fig,'hhg_xW',result.values);
        setappdata(fig,'hhg_axis_source','supplied FWHM calibration with restored wAxis_fromLocs');
    case 'grating_calibration'
        names={'Smoothed maxima (40:70, original auto script)', ...
            'Unsmoothed maxima (60:80, OLD script)', ...
            'FWHM centers + direct fit (40:100, conflicted function)'};
        presets={'smoothed_maxima','old_unsmoothed_maxima','fwhm_direct_fit'};
        [idx,ok]=listdlg('ListString',names,'SelectionMode','single','PromptString','Choose a source calibration preset');
        if ~ok, return; end
        answers=inputdlg({'Harmonic orders matched to descending pixel locations'},'Grating calibration',1,{'11 13 15 17 19 21 23'});
        if isempty(answers), return; end
        orders=sscanf(answers{1},'%f')';
        assert(numel(orders)>=3&&all(isfinite(orders))&&all(diff(orders)>0),'HHG:Calibration','Enter at least three increasing harmonic orders.');
        result.calibration=hhg_analysis_calibrate_grating(h.data,presets{idx},orders,makePlots);
        result.values=result.calibration.xW;
        setappdata(fig,'hhg_xW',result.values);
        setappdata(fig,'hhg_axis_source',['supplied direct grating fit: ' presets{idx}]);
    otherwise
        error('HHG:Action','Unknown analysis workflow: %s',action);
end
setappdata(fig,'hhg_last_result',result);
if makePlots && ~ismember(action,{'calibration','grating_calibration'}), hhg_analysis_plot(fig,result); end
end

function require(names)
missing=names(cellfun(@(n)isempty(which(n)),names));
assert(isempty(missing),'HHG:MissingHelper','Missing helper/toolbox functions: %s. No replacement method was substituted.',strjoin(missing,', '));
end
