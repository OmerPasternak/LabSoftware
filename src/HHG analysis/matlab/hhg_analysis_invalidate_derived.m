function hhg_analysis_invalidate_derived(fig)
%HHG_ANALYSIS_INVALIDATE_DERIVED Preserve geometry/calibration; clear corrected results.
for name={'hhg_reduced','hhg_image_reduced','hhg_trace','hhg_last_result'}
    if isappdata(fig,name{1}), rmappdata(fig,name{1}); end
end
end
