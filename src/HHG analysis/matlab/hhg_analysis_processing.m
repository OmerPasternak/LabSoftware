function processing=hhg_analysis_processing(fig)
%HHG_ANALYSIS_PROCESSING Reproducible description of separate derived analysis.
processing=struct('background_enabled',false,'arithmetic','raw values; no correction', ...
    'sample_mode','legacy_array','binning','none','raw_sources_modified',false);
if isappdata(fig,'hhg_hdf5_source')
    source=getappdata(fig,'hhg_hdf5_source'); processing.sample_mode=source.mode;
    processing.source=source;
end
if isappdata(fig,'hhg_apply_background')&&getappdata(fig,'hhg_apply_background')
    processing.background_enabled=true; bg=getappdata(fig,'hhg_background');
    processing.background=rmfield(bg,'pixels');
    processing.background.scale=0.95;
    processing.arithmetic='sample - reference * 0.95; MATLAB native-class arithmetic (uint16 saturation for raw frames, double for step means)';
end
end
