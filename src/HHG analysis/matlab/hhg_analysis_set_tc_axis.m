function hhg_analysis_set_tc_axis(fig,delay_mm)
%HHG_ANALYSIS_SET_TC_AXIS Attach verified per-frame mechanical TC positions.
% This explicit adapter never interprets shot indices or wall-clock times as
% delay. One finite position in mm is required for every frame, in shot order.
h=guidata(fig);
assert(h.loaded&&isvector(delay_mm)&&numel(delay_mm)==h.flength&&all(isfinite(delay_mm)), ...
    'HHG:TCAxis','Provide one finite mechanical TC position in mm per loaded frame.');
h.delay=double(delay_mm(:)'); guidata(fig,h);
setappdata(fig,'hhg_tc_confirmed',true); setappdata(fig,'hhg_delay_label','TC position (mm)');
hhg_analysis_render(fig);
end
