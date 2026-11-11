function hhg_analysis_select_roi(fig,rect)
%HHG_ANALYSIS_SELECT_ROI Select a bounded bottom-origin [x,y,width,height] ROI.
% Exactly width*height pixels enter display, trace and workspace export.
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load an image or scan first.');
if nargin<2
    axes(h.axes1); zoom off;
    if exist('getrect','file')==2
        rect=round(getrect(h.axes1));
    else
        title(h.axes1,'Click two ROI corners (Image Processing Toolbox is unavailable)');
        [x,y]=ginput(2); title(h.axes1,'');
        if numel(x)~=2, return; end
        rect=round([min(x) min(y) abs(diff(x)) abs(diff(y))]);
    end
end
assert(numel(rect)==4&&all(isfinite(rect)),'HHG:ROI','ROI must contain four finite coordinates.');
rect=round(rect);
h.roix=max(1,min(h.width,rect(1))); h.roiy=max(1,min(h.height,rect(2)));
h.roiwidth=max(1,min(h.width-h.roix+1,rect(3)));
h.roiheight=max(1,min(h.height-h.roiy+1,rect(4))); h.roi=1;
guidata(fig,h);
if isappdata(fig,'hhg_trace'), rmappdata(fig,'hhg_trace'); end
if isappdata(fig,'hhg_hdf5_source'), hhg_analysis_trace(fig);
else, hhg_analysis_render(fig); end
end
