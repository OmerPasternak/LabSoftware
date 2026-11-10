function hhg_analysis_export_workspace(fig)
%HHG_ANALYSIS_EXPORT_WORKSPACE Publish the original ShowScanGUI contract.
% Read batches into a normal MATLAB [x,y,sample] array only on explicit export.
% Publish after successful reads; errors leave the previous workspace intact.
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load data first.');
example=hhg_analysis_read(fig,1,1); info=whos('example');
bytes=double(h.width)*h.height*h.flength*info.bytes/numel(example);
try
    available=memory;
    assert(bytes<0.7*available.MemAvailableAllArrays,'HHG:Memory', ...
        'Not enough RAM to export the full data array. Use the batched MAT export instead.');
catch e
    if strcmp(e.identifier,'HHG:Memory'), rethrow(e); end
end
data=zeros(h.width,h.height,h.flength,'like',example);
batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1);
    data(:,:,first:first+count-1)=hhg_analysis_read(fig,first,count);
    drawnow limitrate nocallbacks;
end
processing=struct('raw_sources_modified',false);
if exist('hhg_analysis_processing','file')==2, processing=hhg_analysis_processing(fig); end
bundle=struct('data',data,'delay',squeeze(h.delay),'filename',h.filename, ...
    'hhg_source',getappdata(fig,'hhg_hdf5_source'),'hhg_processing',processing);
if h.roi
    rows=h.height-(h.roiy:h.roiy+h.roiheight-1)+1;
    bundle.roidata=squeeze(data(h.roix:h.roix+h.roiwidth-1,rows,:));
    bundle.roidataint=squeeze(sum(sum(bundle.roidata,1),2));
    bundle.roix=h.roix; bundle.roiy=h.roiy;
    bundle.roiwidth=h.roiwidth; bundle.roiheight=h.roiheight;
end
if h.flength==1
    lineout=squeeze(sum(data,2)); bundle.lineout=lineout-min(lineout);
end
names=fieldnames(bundle);
for k=1:numel(names), assignin('base',names{k},bundle.(names{k})); end
end
