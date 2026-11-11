function hhg_analysis_validate_options(opts,h)
%HHG_ANALYSIS_VALIDATE_OPTIONS Reject ambiguous ranges before numerical work.
check_range(opts.rows,h.height); check_range(opts.columns,h.width);
assert(isscalar(opts.slice)&&opts.slice>=1&&opts.slice<=h.width&&opts.slice==fix(opts.slice), ...
    'HHG:Settings','Pearson row slice is outside the spectral width.');
assert(numel(opts.harmonic_limits)==2&&all(isfinite(opts.harmonic_limits))&&diff(opts.harmonic_limits)>0, ...
    'HHG:Settings','Enter two increasing harmonic display limits.');
assert(isscalar(opts.tc_step)&&isfinite(opts.tc_step)&&opts.tc_step>0, ...
    'HHG:Settings','TC step must be a positive scalar in mm.');
assert(isscalar(opts.tc_fs)&&ismember(opts.tc_fs,[0 1])&&isscalar(opts.tc_subtract)&&ismember(opts.tc_subtract,[0 1]), ...
    'HHG:Settings','TC toggles must be 0 or 1.');
end

function check_range(bounds,n)
assert(numel(bounds)==2&&all(isfinite(bounds))&&all(bounds==fix(bounds))&&bounds(1)>=1&& ...
    bounds(2)>=bounds(1)&&bounds(2)<=n,'HHG:Settings','A pixel window is outside the loaded image.');
end
