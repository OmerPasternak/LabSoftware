function trace = hhg_analysis_trace(fig)
%HHG_ANALYSIS_TRACE Explicit, batched total/ROI intensity for every sample.
h=guidata(fig); trace=zeros(h.flength,1); batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1); data=hhg_analysis_read(fig,first,count);
    if h.roi
        data=flip(data,2);
        data=data(h.roix:h.roix+h.roiwidth-1,h.roiy:h.roiy+h.roiheight-1,:);
    end
    trace(first:first+count-1)=reshape(sum(sum(data,1),2),[],1);
    drawnow limitrate nocallbacks;
end
setappdata(fig,'hhg_trace',trace); hhg_analysis_render(fig);
end
