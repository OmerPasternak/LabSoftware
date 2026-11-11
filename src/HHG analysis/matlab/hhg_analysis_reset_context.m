function hhg_analysis_reset_context(fig,axisLabel)
%HHG_ANALYSIS_RESET_CONTEXT Invalidate selections/calibration tied to old data.
names={'hhg_options','hhg_xW','hhg_axis_source','hhg_last_result','hhg_tc_confirmed', ...
    'hhg_reduced','hhg_image_reduced','hhg_trace'};
for k=1:numel(names)
    if isappdata(fig,names{k}), rmappdata(fig,names{k}); end
end
setappdata(fig,'hhg_delay_label',axisLabel);
end
