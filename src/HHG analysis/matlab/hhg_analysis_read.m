function data = hhg_analysis_read(fig,first,count)
%HHG_ANALYSIS_READ Bounded raw-frame access in MATLAB [x,y,frame] order.
% Current schema-2.0 HDF5 is read by hyperslab; legacy arrays stay supported.
h=guidata(fig);
assert(first>=1&&count>=1&&first+count-1<=h.flength,'HHG:Frames','Requested frame range is outside the source.');
if isappdata(fig,'hhg_hdf5_source')
    source=getappdata(fig,'hhg_hdf5_source');
    if strcmp(source.mode,'frames')
        data=h5read(source.paths{1},'/images',[1 1 first],[h.width h.height count]);
    else
        % One derived sample per scan step, never one artificial delay per
        % camera frame. All repetitions in each step enter its mean equally.
        data=zeros(h.width,h.height,count);
        for j=1:count
            index=first+j-1; n=source.frame_counts(index); total=zeros(h.width,h.height);
            batch=hhg_analysis_batch_size(h.width,h.height);
            for start=1:batch:n
                frames=h5read(source.paths{index},'/images',[1 1 start],[h.width h.height min(batch,n-start+1)]);
                total=total+sum(double(frames),3);
            end
            data(:,:,j)=total/n;
        end
    end
else
    data=h.data(:,:,first:first+count-1);
end
if isappdata(fig,'hhg_hdf5_source')&&isappdata(fig,'hhg_apply_background')&&getappdata(fig,'hhg_apply_background')
    reference=getappdata(fig,'hhg_background');
    assert(isequal(size(reference.pixels),[h.width h.height]),'HHG:Reference','Background geometry differs.');
    if isfield(reference,'source')
        keys={'roi_bounds','exposure_time_s','readout_mode','camera_serial'};
        source=getappdata(fig,'hhg_hdf5_source');
        for k=1:numel(keys)
            assert(isequal(h5readatt(reference.source,'/',keys{k}),h5readatt(source.paths{1},'/',keys{k})), ...
                'HHG:Reference','Background %s metadata differs.',keys{k});
        end
    end
    data=data-reference.pixels*0.95;
end
end
