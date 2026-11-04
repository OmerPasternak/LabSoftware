function hhg_analysis_fit_window(fig)
%HHG_ANALYSIS_FIT_WINDOW Fit the original fixed GUIDE canvas to this screen.
% Normalize against the complete design bounds, including controls/axes that
% the legacy loader placed outside a smaller monitor's figure. Data axes,
% colormaps, orientation and plot values are unaffected.
if isappdata(fig,'hhg_layout_fitted'), return; end
set(fig,'Units','pixels'); position=get(fig,'Position');
objects=findall(fig,'Type','uicontrol','-or','Type','uipanel','-or', ...
    'Type','uibuttongroup','-or','Type','axes');
required=position(3:4);
rootObjects=gobjects(0); rootBounds=zeros(0,4);
for k=1:numel(objects)
    bounds=getpixelposition(objects(k),true);
    required=max(required,bounds(1:2)+bounds(3:4)+[12 12]);
    if isequal(get(objects(k),'Parent'),fig)
        rootObjects(end+1)=objects(k); rootBounds(end+1,:)=bounds; %#ok<AGROW>
    end
end
handles=guidata(fig); imageBounds=getpixelposition(handles.axes1,true); split=imageBounds(1);
position(3:4)=required; set(fig,'Position',position);
for k=1:numel(objects), set(objects(k),'Units','normalized'); end
screen=get(groot,'ScreenSize'); available=max([400 300],screen(3:4)-[40 100]);
targetSize=min(required,available);
position=[screen(1:2)+(screen(3:4)-targetSize)/2 targetSize];
set(fig,'Position',position,'Resize','on');
% Keep the control column's original readable widths. Compress the plot
% region independently, and use the available height rather than shrinking
% every label/font to a tiny uniform thumbnail.
rightScale=max(.1,(targetSize(1)-split)/(required(1)-split));
for k=1:numel(rootObjects)
    bounds=rootBounds(k,:);
    if bounds(1)>=split-1
        bounds(1)=split+(bounds(1)-split)*rightScale; bounds(3)=bounds(3)*rightScale;
    end
    bounds([2 4])=bounds([2 4])*(targetSize(2)/required(2));
    set(rootObjects(k),'Units','pixels','Position',bounds,'Units','normalized');
end
setappdata(fig,'hhg_layout_fitted',true);
end
