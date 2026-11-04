function hhg_analysis_render(fig)
%HHG_ANALYSIS_RENDER Explicit axes, one raw frame at a time, original orientation.
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load an image or scan first.');
frame=hhg_analysis_read(fig,h.nsel,1);
cla(h.axes1); cla(h.axes2);
if h.scale==0
    if min(size(frame))>1, pcolor(h.axes1,h.x,h.y,frame'); shading(h.axes1,'flat');
    else, imagesc(h.axes1,h.x,h.y,frame'); end
    caxis(h.axes1,[0 max(1,h.maxval)]);
else
    pcolor(h.axes1,h.x,h.y,log10(double(frame')+0.1)); shading(h.axes1,'flat');
    lo=max(.1,min(h.minval,max(.1,h.maxval)/10)); hi=max(lo*10,h.maxval);
    caxis(h.axes1,log10([lo hi]));
end
xlabel(h.axes1,'x (px)'); ylabel(h.axes1,'y (px)'); colorbar(h.axes1);
lineout=sum(frame,2);
if h.scale==0, plot(h.axes2,h.x,lineout,'LineWidth',1.5);
else, semilogy(h.axes2,h.x,lineout,'LineWidth',1.5); end
xlabel(h.axes2,'x (px)'); ylabel(h.axes2,'Intensity (arb.u.)'); xlim(h.axes2,hhg_analysis_limits(h.x));
if h.roi
    roi=flip(frame,2); roi=roi(h.roix:h.roix+h.roiwidth-1,h.roiy:h.roiy+h.roiheight-1);
    rectangle(h.axes1,'Position',[h.roix h.roiy h.roiwidth h.roiheight],'EdgeColor','w');
    cla(h.axes5);
    if min(h.roiwidth,h.roiheight)>1, pcolor(h.axes5,roi'); shading(h.axes5,'flat');
    else, imagesc(h.axes5,roi'); end
    colorbar(h.axes5);
    set(h.text8,'String',sprintf('x %d, y %d',h.roix,h.roiy));
    set(h.text10,'String',sprintf('w %d, h %d',h.roiwidth,h.roiheight));
else
    cla(h.axes5); colorbar(h.axes5,'off');
    set(h.text8,'String','ROI: none'); set(h.text10,'String',' ');
end
if isappdata(fig,'hhg_trace')
    trace=getappdata(fig,'hhg_trace');
elseif ~isappdata(fig,'hhg_hdf5_source')
    data=h.data;
    if h.roi
        data=flip(data,2); data=data(h.roix:h.roix+h.roiwidth-1,h.roiy:h.roiy+h.roiheight-1,:);
    end
    trace=reshape(sum(sum(data,1),2),[],1);
else
    trace=[];
end
cla(h.axes3);
label=getappdata(fig,'hhg_delay_label'); if isempty(label), label='Piezo position (um)'; end
if ~isempty(trace)
    plot(h.axes3,h.delay,trace,'-k*'); xlim(h.axes3,hhg_analysis_limits(h.delay));
    set(h.text17,'String',sprintf('Mean ROI/full counts: %.8g',mean(trace)));
else
    text(h.axes3,.1,.5,'HHG Analysis > Compute total / ROI trace','Units','normalized');
    xlim(h.axes3,hhg_analysis_limits(h.delay));
    set(h.text17,'String','Trace: compute from HHG Analysis menu');
end
xlabel(h.axes3,label); ylabel(h.axes3,'Intensity (arb.u.)');
set(h.text11,'String',sprintf('%d/%d',h.nsel,h.flength));
frameMode=false;
if isappdata(fig,'hhg_hdf5_source')
    source=getappdata(fig,'hhg_hdf5_source'); frameMode=strcmp(source.mode,'frames');
end
if frameMode
    set(h.text7,'String',sprintf('Frame %d/%d',h.nsel,h.flength));
else
    set(h.text7,'String',sprintf('%s: %.8g',label,h.delay(h.nsel)));
end
end
