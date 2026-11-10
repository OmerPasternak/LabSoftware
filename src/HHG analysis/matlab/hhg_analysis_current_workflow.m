function r = hhg_analysis_current_workflow(fig,action,opts,makePlots)
%HHG_ANALYSIS_CURRENT_WORKFLOW MATLAB lab equations on batched schema-2.0 data.
h=guidata(fig); source=getappdata(fig,'hhg_hdf5_source'); xW=getappdata(fig,'hhg_xW');
r=struct('action',action,'source',source,'settings',opts,'frame_count',sum(source.frame_counts), ...
    'sample_count',h.flength,'sample_mode',source.mode, ...
    'xW',xW,'axis_source',getappdata(fig,'hhg_axis_source'),'created_utc', ...
    char(datetime('now','TimeZone','UTC','Format','yyyy-MM-dd''T''HH:mm:ssXXX')));
if isappdata(fig,'hhg_apply_background')&&getappdata(fig,'hhg_apply_background')
    bg=getappdata(fig,'hhg_background'); r.background=rmfield(bg,'pixels');
end
images=ismember(action,{'dc','variance_image','grating_calibration'});
covariance=ismember(action,{'pearson','covariance','flipped_pearson','calibrated_pearson'});
if images
    reduced=hhg_analysis_reduce(fig,opts,true,false);
elseif ~ismember(action,{'single_image','tc','tc_fft','calibration'})
    if strcmp(action,'legacy_space'), opts.rows=[1 h.height]; r.settings=opts; end
    assert(h.flength>=2||strcmp(action,'repeats'),'HHG:Frames','Sample statistics need at least two observations.');
    if strcmp(action,'pearson_slice')
        reduced=hhg_analysis_reduce(fig,opts,false,false,opts.slice);
    else
        reduced=hhg_analysis_reduce(fig,opts,false,covariance);
    end
    r.mean_spectrum=reduced.mean_spectrum;
    if ismember(action,{'pearson','flipped_pearson','calibrated_pearson','pearson_slice'})
        stdev=sqrt(reduced.variance_spectrum(:));
        rows=1:numel(stdev); if strcmp(action,'pearson_slice'), rows=opts.slice; end
        if isfield(reduced,'covariance'), cross=reduced.covariance(rows,:);
        else, cross=reduced.covariance_slice; end
        pearson=cross./(stdev(rows)*stdev');
        pearson=min(1,max(-1,pearson));
        pearson(stdev(rows)==0,:)=NaN; pearson(:,stdev==0)=NaN;
    end
end
switch action
    case 'dc', r.values=reduced.mean_image';
    case 'variance_image'
        assert(h.flength>=2,'HHG:Frames','Sample variance needs two observations.');
        r.values=reduced.variance_image';
        r.estimator='batched centered N-1 variance, returned single precision; rounding may differ from whole-stack var(single)';
    case {'pearson','flipped_pearson','calibrated_pearson'}
        r.values=pearson;
        if strcmp(action,'calibrated_pearson'), assert(~isempty(xW),'HHG:Calibration','Import a validated xW axis first.'); end
    case 'covariance', r.values=reduced.covariance;
    case 'covariance_diagonal', r.values=reduced.variance_spectrum(:);
    case 'pearson_slice', r.values=pearson;
    case 'variance_line', r.values=reduced.variance_spectrum;
    case {'repeats','legacy_space','pca_frequency','pca_space'}
        if ~strcmp(action,'pca_space')
            assert(~isempty(reduced.spectra),'HHG:Memory','This view exceeds the reduced-spectrum memory budget; select a smaller source. No frames were omitted.');
        end
        switch action
            case 'repeats', r.values=reduced.spectra';
            case 'legacy_space'
                cols=opts.columns(1):opts.columns(2); r.values=fliplr(corrcoef(reduced.spectra(:,cols)));
            otherwise
                if strcmp(action,'pca_space'), observations=reduced.spatial;
                else, observations=reduced.spectra; end
                assert(~isempty(observations),'HHG:Memory','Reduced PCA observations exceed the memory budget.');
                [r.coefficients,r.scores,r.latent,r.pca_method]=hhg_analysis_pca(observations);
                r.explained_fraction=r.latent/sum(r.latent); r.values=r.coefficients;
        end
    case 'single_image'
        r.values=flipud(fliplr(hhg_analysis_read(fig,h.nsel,1)'));
    case 'tc'
        assert(isappdata(fig,'hhg_tc_confirmed'),'HHG:TCAxis', ...
            'Confirm the metadata scan axis represents mechanical TC positions in mm first. Repetition indices are not delays.');
        assert(h.flength>=2&&all(abs(diff(h.delay)-opts.tc_step)<1e-7),'HHG:TCAxis','TC requires uniformly spaced metadata positions at the configured step.');
        assert(any(abs(opts.tc_step-[.001 .002 .003 .005 .01 .05])<1e-9),'HHG:TC','Unsupported historical TC calibration step.');
        assert(8*double(h.width)*h.flength<=256*1024^2,'HHG:Memory','TC reduced map exceeds 256 MiB.');
        r.values=zeros(h.width,h.flength); rows=opts.rows(1):opts.rows(2);
        batch=hhg_analysis_batch_size(h.width,h.height);
        for first=1:batch:h.flength
            count=min(batch,h.flength-first+1); data=hhg_analysis_read(fig,first,count);
            if opts.tc_subtract
                assert(isappdata(fig,'hhg_tc_reference'),'HHG:Reference','Capture a coherent-only reference first.');
                ref=getappdata(fig,'hhg_tc_reference');
                assert(isequal(size(ref.pixels),[h.width h.height]),'HHG:Reference','Reference geometry differs.');
                data=data-ref.pixels; r.reference=rmfield(ref,'pixels');
            end
            r.values(:,first:first+count-1)=reshape(mean(data(:,rows,:),2),h.width,count);
        end
        r.delay_mm=h.delay; r.delay_fs=(1:h.flength)*opts.tc_step/(.001*5.8*4)*2.67;
    case 'grating_calibration'
        presets={'smoothed_maxima','old_unsmoothed_maxima','fwhm_direct_fit'};
        [idx,ok]=listdlg('ListString',presets,'SelectionMode','single','PromptString','Choose the original calibration preset');
        if ~ok, return; end
        answer=inputdlg({'Harmonic orders, in descending pixel-location order'},'Calibration',1,{'11 13 15 17 19 21 23'});
        if isempty(answer), return; end
        orders=sscanf(answer{1},'%f')';
        assert(numel(orders)>=3&&all(isfinite(orders))&&all(diff(orders)>0),'HHG:Calibration','Enter increasing harmonic orders.');
        r.calibration=hhg_analysis_calibrate_grating(reduced.mean_image,presets{idx},orders,makePlots);
        r.values=r.calibration.xW; setappdata(fig,'hhg_xW',r.values);
        setappdata(fig,'hhg_axis_source',['direct grating fit: ' presets{idx}]);
    otherwise
        error('HHG:Inactive','This historical helper-dependent workflow is inactive. See INACTIVE_FEATURES.txt.');
end
r.processing=hhg_analysis_processing(fig);
r.reduction='source MATLAB spatial MEAN; batched current-camera samples';
setappdata(fig,'hhg_last_result',r);
if makePlots&&~strcmp(action,'grating_calibration'), hhg_analysis_plot(fig,r); end
end
