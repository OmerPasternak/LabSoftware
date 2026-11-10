function reduced = hhg_analysis_reduce(fig,opts,imageMoments,needCovariance,covarianceRows)
%HHG_ANALYSIS_REDUCE Cache vectorized reductions, not raw camera stacks.
% Batchwise centered covariance uses the parallel/Welford merge equations.
% All frames contribute; source order and pixels are never altered.
% Corrections are part of cache identity, including externally toggled appdata.
enabled=isappdata(fig,'hhg_apply_background')&&getappdata(fig,'hhg_apply_background');
key=struct('enabled',enabled);
if enabled, key.reference=getappdata(fig,'hhg_background'); end
if ~isappdata(fig,'hhg_correction_key')||~isequal(getappdata(fig,'hhg_correction_key'),key)
    hhg_analysis_invalidate_derived(fig); setappdata(fig,'hhg_correction_key',key);
end
if nargin<3, imageMoments=false; end
if nargin<4, needCovariance=true; end
if nargin<5, covarianceRows=[]; end
if imageMoments, reduced=image_moments(fig); return; end
if isappdata(fig,'hhg_reduced')
    cached=getappdata(fig,'hhg_reduced');
    sliceAvailable=isempty(covarianceRows)||isfield(cached,'covariance')|| ...
        (isfield(cached,'slice_rows')&&isequal(cached.slice_rows,covarianceRows));
    if isequal(cached.rows,opts.rows)&&isequal(cached.columns,opts.columns)&&(~needCovariance||isfield(cached,'covariance'))&&sliceAvailable
        reduced=cached; return;
    end
end
h=guidata(fig); rows=opts.rows(1):opts.rows(2); cols=opts.columns(1):opts.columns(2);
reduced=struct('rows',opts.rows,'columns',opts.columns,'count',0, ...
    'mean_spectrum',zeros(1,h.width),'spectra',[],'spatial',[]);
if needCovariance
    assert(4*8*double(h.width)^2<=1024^3,'HHG:Memory','Covariance workspace exceeds 1 GiB. Choose an explicit smaller spectral ROI.');
    reduced.M2=zeros(h.width);
else
    reduced.M2=zeros(1,h.width);
end
if ~isempty(covarianceRows)
    reduced.slice_rows=covarianceRows; reduced.cross_M2=zeros(numel(covarianceRows),h.width);
end
% Reduced spectra are retained for repeats/PCA when at most 256 MiB.
if 8*double(h.flength)*h.width<=256*1024^2, reduced.spectra=zeros(h.flength,h.width); end
if 8*double(h.flength)*h.height<=128*1024^2, reduced.spatial=zeros(h.flength,h.height); end
batch=hhg_analysis_batch_size(h.width,h.height);
% Separate disk batches from matrix batches: rank-256 BLAS updates reuse
% the covariance matrix much better than one rank-eight update per HDF5 read.
pending=zeros(256+batch,h.width); pendingCount=0; reduced.matrix_updates=0;
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1); data=hhg_analysis_read(fig,first,count);
    spectra=reshape(mean(data(:,rows,:),2),h.width,count)';
    pending(pendingCount+1:pendingCount+count,:)=spectra; pendingCount=pendingCount+count;
    if ~isempty(reduced.spectra), reduced.spectra(first:first+count-1,:)=spectra; end
    if ~isempty(reduced.spatial)
        reduced.spatial(first:first+count-1,:)=reshape(mean(data(cols,:,:),1),h.height,count)';
    end
    if pendingCount>=256||first+count-1==h.flength
        values=pending(1:pendingCount,:); localMean=mean(values,1); centered=values-localMean;
        total=reduced.count+pendingCount; delta=localMean-reduced.mean_spectrum;
        weight=reduced.count*pendingCount/total;
        if needCovariance
            reduced.M2=reduced.M2+centered'*centered+(delta'*delta)*weight;
        else
            reduced.M2=reduced.M2+sum(centered.^2,1)+delta.^2*weight;
        end
        if ~isempty(covarianceRows)
            reduced.cross_M2=reduced.cross_M2+centered(:,covarianceRows)'*centered+delta(covarianceRows)'*delta*weight;
        end
        reduced.mean_spectrum=reduced.mean_spectrum+delta*(pendingCount/total);
        reduced.count=total; reduced.matrix_updates=reduced.matrix_updates+1; pendingCount=0;
    end
    drawnow limitrate nocallbacks;
end
if needCovariance
    reduced.covariance=reduced.M2/max(1,reduced.count-1);
    reduced.variance_spectrum=diag(reduced.covariance)';

else
    reduced.variance_spectrum=reduced.M2/max(1,reduced.count-1);
end
if ~isempty(covarianceRows)
    reduced.covariance_slice=reduced.cross_M2/max(1,reduced.count-1);
    reduced=rmfield(reduced,'cross_M2');
end
reduced=rmfield(reduced,'M2');
setappdata(fig,'hhg_reduced',reduced);
end

function reduced=image_moments(fig)
if isappdata(fig,'hhg_image_reduced'), reduced=getappdata(fig,'hhg_image_reduced'); return; end
h=guidata(fig); n=0; average=zeros(h.width,h.height); m2=zeros(h.width,h.height);
batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1); values=double(hhg_analysis_read(fig,first,count));
    localMean=mean(values,3); delta=localMean-average; total=n+count;
    m2=m2+sum((values-localMean).^2,3)+delta.^2*(n*count/total);
    average=average+delta*(count/total); n=total;
    drawnow limitrate nocallbacks;
end
reduced=struct('mean_image',average,'variance_image',single(m2/max(1,n-1)),'count',n);
setappdata(fig,'hhg_image_reduced',reduced);
end
