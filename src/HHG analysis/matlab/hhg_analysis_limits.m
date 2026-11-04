function limits = hhg_analysis_limits(values)
%HHG_ANALYSIS_LIMITS Valid plot bounds, including a one-point scan.
limits=double([min(values(:)) max(values(:))]);
if limits(1)==limits(2), limits=limits+[-0.5 0.5]; end
end
