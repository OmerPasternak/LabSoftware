function [opts,accepted] = hhg_analysis_configure(fig, interactive)
%HHG_ANALYSIS_CONFIGURE Explicit one-based inclusive analysis windows.
if nargin<2, interactive=true; end
accepted=true;
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load an image or scan first.');
if isappdata(fig,'hhg_options')
    opts=getappdata(fig,'hhg_options');
else
    if h.height>=140, rows=[110 140]; else, rows=[1 h.height]; end
    if h.width>=1190, cols=[1150 1190]; else, cols=[1 h.width]; end
    opts=struct('rows',rows,'columns',cols,'slice',min(400,h.width), ...
        'harmonic_limits',[11 29],'tc_step',0.001,'tc_fs',true,'tc_subtract',false);
end
if ~interactive, return; end
answers=inputdlg({
    'Spatial rows: first last (one-based inclusive; covariance/PCA uses a MEAN)';
    'Spectral pixels: first last (spatial PCA / legacy space-covariance block)';
    'Pearson row slice (one-based index)';
    'Calibrated harmonic display limits: lower upper';
    'TC mechanical step (mm; original 1600 nm empirical calibration)';
    'TC display in fs? 1=yes, 0=mechanical mm';
    'TC subtract frozen coherent-only reference? 1=yes, 0=no'}, ...
    'HHG analysis settings',1,{num2str(opts.rows),num2str(opts.columns),num2str(opts.slice), ...
    num2str(opts.harmonic_limits),num2str(opts.tc_step),num2str(opts.tc_fs),num2str(opts.tc_subtract)});
if isempty(answers), accepted=false; return; end
fields={'rows','columns','slice','harmonic_limits','tc_step','tc_fs','tc_subtract'};
for k=1:numel(fields)
    values=sscanf(answers{k},'%f')';
    assert(~isempty(values)&&all(isfinite(values)),'HHG:Settings','Enter finite numeric values only.');
    opts.(fields{k})=values;
end
hhg_analysis_validate_options(opts,h);
setappdata(fig,'hhg_options',opts);
end
