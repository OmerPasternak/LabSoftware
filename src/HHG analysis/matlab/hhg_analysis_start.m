function fig = hhg_analysis_start(inputPath,action)
%HHG_ANALYSIS_START Open the preserved scan analyzer and offline lab workflows.
% All paths are relative to this file. Acquisition code is never loaded.
root = fileparts(mfilename('fullpath'));
addpath(root, fullfile(root,'active'));
fig = ShowScanGUI;
hhg_analysis_fit_window(fig);
if isappdata(fig,'hhg_analysis_initialized')
    figure(fig);
    if nargin>=1&&~isempty(inputPath), hhg_analysis_load_hdf5(fig,inputPath); end
    if nargin>=2&&~isempty(action), hhg_analysis_workflow(fig,action); end
    return;
end
setappdata(fig,'hhg_analysis_initialized',true);
setappdata(fig,'hhg_reference_colormap',colormap(fig));
set(fig,'Name','ShowScanGUI — HHG Analysis');
menu = uimenu(fig,'Label','HHG Analysis');
uimenu(menu,'Label','Load completed camera HDF5 (raw pixels)...','Callback',@(~,~)safe(@()load_hdf5(fig)));
uimenu(menu,'Label','Load current HDF5 scan (one mean per metadata step)...','Callback',@(~,~)safe(@()load_scan(fig)));
uimenu(menu,'Label','Compute total / ROI trace (batched)','Callback',@(~,~)safe(@()hhg_analysis_trace(fig)));
uimenu(menu,'Label','Confirm loaded scan positions as TC mechanical mm...', ...
    'Callback',@(~,~)safe(@()confirm_tc(fig)));
uimenu(menu,'Label','Apply captured background to derived HDF5 reads (0.95x)', ...
    'Callback',@(src,~)safe(@()toggle_background(fig,src)));
uimenu(menu,'Label','Export complete source to MAT in batches...', ...
    'Callback',@(~,~)safe(@()hhg_analysis_export_mat(fig)));
uimenu(menu,'Label','Configure spatial/spectral ranges...','Callback',@(~,~)safe(@()hhg_analysis_configure(fig)));
uimenu(menu,'Label','Import saved harmonic axis xW...','Callback',@(~,~)safe(@()import_axis(fig)));
uimenu(menu,'Label','Capture current frame as TC coherent-only reference','Callback',@(~,~)safe(@()capture_tc(fig)));
uimenu(menu,'Label','Selected ROI: per-pixel Fourier (aggregation inactive)', ...
    'Callback',@(~,~)safe(@()roi_fourier(fig)));
items = {
    'DC mean image (log, 90 to 200)', 'dc';
    'Spectral Pearson correlation (-1 to 1)', 'pearson';
    'Spectral sample covariance (N-1)', 'covariance';
    'Unsorted statistical repeats (90 to 130)', 'repeats';
    'Flipped Pearson + legacy spectrum overlay', 'flipped_pearson';
    'Calibrated Pearson + harmonic guides', 'calibrated_pearson';
    'Covariance diagonal', 'covariance_diagonal';
    'Pearson row slice', 'pearson_slice';
    'Pixel variance image (single precision, N-1)', 'variance_image';
    'Variance of spatially averaged spectrum', 'variance_line';
    'Legacy space-covariance block (see guide)', 'legacy_space';
    'Spatial PCA', 'pca_space';
    'Spectral PCA', 'pca_frequency';
    'Current image (legacy double flip)', 'single_image';
    'TC delay map / optional coherent-only subtraction', 'tc';
    'TC Fourier (unknown historical fftPerCell convention)', 'tc_fft';
    'Automatic FWHM calibration (requires unavailable wAxis_fromLocs)', 'calibration';
    'Direct grating calibration (merged complete fit variants)', 'grating_calibration';
    };
for k=1:size(items,1)
    action=items{k,2};
    label=items{k,1}; inactive=ismember(action,{'tc_fft','calibration'});
    if inactive, label=['[INACTIVE] ' label]; end
    if strcmp(action,'grating_calibration')&& (isempty(which('findpeaks'))||isempty(which('fit')))
        label=['[TOOLBOX REQUIRED] ' label];
    end
    uimenu(menu,'Label',label,'Enable',onoff(~inactive),'Separator',onoff(k==1), ...
        'Callback',@(~,~)safe(@()hhg_analysis_workflow(fig,action)));
end
uimenu(menu,'Label','Export analysis result + settings to new MAT file...', ...
    'Separator','on','Callback',@(~,~)safe(@()save_result(fig)));
uimenu(menu,'Label','Review original scripts (sections; do not run blindly)', ...
    'Callback',@(~,~)safe(@()review_sources(root)));
uimenu(menu,'Label','Feature guide / missing dependencies', ...
    'Callback',@(~,~)edit(fullfile(root,'..','docs','feature_guide.md')));
% Route legacy controls through guards, retaining the original implementations.
tags={'pushbutton1','pushbutton13','pushbutton22','pushbutton3','pushbutton14', ...
    'pushbutton15','pushbutton19','pushbutton20','pushbutton21','pushbutton25', ...
    'pushbutton26','pushbutton17','pushbutton23','pushbutton24','pushbutton27', ...
    'slider1','radiobutton1','radiobutton2','checkbox1','checkbox2'};
for k=1:numel(tags)
    tag=tags{k}; h=findall(fig,'Tag',tag);
    set(h,'Callback',@(src,event)safe(@()legacy(fig,src,event)));
end
set(findall(fig,'Tag','pushbutton2'),'Callback',@(~,~)safe(@()hhg_analysis_select_roi(fig)));
fourier={'pushbutton17','pushbutton23','pushbutton24','pushbutton27'};
for k=1:numel(fourier)
    set(findall(fig,'Tag',fourier{k}),'Enable','on', ...
        'TooltipString','Recreated per-pixel |FFT|. Integrated aggregation is inactive because its historical definition is unknown.');
end
set(findall(fig,'Tag','pushbutton18'),'Enable','off', ...
    'TooltipString','The supplied Convert to spectrum callback is empty.');
set(findall(fig,'Tag','slider2'),'Enable','off', ...
    'TooltipString','The supplied secondary slider has no implementation.');
% Prominent controls use the current acquisition format. Preserve every old
% loader under an explicit compatibility submenu, not as the default path.
set(findall(fig,'Tag','pushbutton1'),'String','Load Scan ...', ...
    'TooltipString','Open one current camera HDF5 file; repetitions remain individual statistical samples.', ...
    'Callback',@(~,~)safe(@()load_hdf5(fig)));
set(findall(fig,'Tag','pushbutton13'),'String','Load Image ...', ...
    'TooltipString','Open a current HDF5 reference file, then choose the desired reference frame.', ...
    'Callback',@(~,~)safe(@()load_hdf5(fig)));
set(findall(fig,'Tag','pushbutton22'),'String','Load Two Color Scan ...', ...
    'Callback',@(~,~)safe(@()load_scan(fig)));
compatibility=uimenu(menu,'Label','Legacy binary inputs (compatibility)');
uimenu(compatibility,'Label','TXT + BIN scan...', ...
    'Callback',@(~,~)safe(@()legacy(fig,findall(fig,'Tag','pushbutton1'),[])));
uimenu(compatibility,'Label','PIC + TXT image...', ...
    'Callback',@(~,~)safe(@()legacy(fig,findall(fig,'Tag','pushbutton13'),[])));
uimenu(compatibility,'Label','TCS + BIN scan...', ...
    'Callback',@(~,~)safe(@()legacy(fig,findall(fig,'Tag','pushbutton22'),[])));
fprintf(['HHG analysis ready. Original legacy loaders may replace the first scan pixel,\n' ...
    'crop 1392x1040 images and subtract 0.95*bg with uint16 saturation.\n' ...
    'HDF5 import does none of these. See the feature guide before comparing estimators.\n']);
if nargin>=1&&~isempty(inputPath), hhg_analysis_load_hdf5(fig,inputPath); end
if nargin>=2&&~isempty(action), hhg_analysis_workflow(fig,action); end
end

function legacy(fig,src,event)
tag=get(src,'Tag'); h=guidata(fig);
serial=0; if isfield(h,'hhg_load_serial'), serial=h.hhg_load_serial; end
withoutData={'pushbutton1','pushbutton13','pushbutton22','pushbutton20','pushbutton26'};
if ~ismember(tag,withoutData) && ~h.loaded
    error('HHG:NoData','Load an image or scan first.');
end
if isappdata(fig,'hhg_hdf5_source')&&~ismember(tag,{'pushbutton1','pushbutton13','pushbutton22'})
    hhg_analysis_current_action(fig,src,event); return;
end
ShowScanGUI([tag '_Callback'],src,event,h);
h=guidata(fig);
if h.loaded
    set(h.slider1,'Enable',onoff(h.flength>1));
    set([h.radiobutton1 h.radiobutton2],'Enable','on');
    if ismember(tag,{'pushbutton1','pushbutton13','pushbutton22'})&&isfield(h,'hhg_load_serial')&&h.hhg_load_serial~=serial
        if isappdata(fig,'hhg_hdf5_source'), rmappdata(fig,'hhg_hdf5_source'); end
        set(h.pushbutton15,'String','Export data to workspace');
        if strcmp(tag,'pushbutton22'), label='TC position (mm)'; else, label='Piezo position (um)'; end
        hhg_analysis_reset_context(fig,label);
    end
    if ismember(tag,{'slider1','radiobutton1','radiobutton2','pushbutton3'})
        hhg_analysis_render(fig);
    end
end
end

function load_scan(fig)
[files,p]=uigetfile({'*.h5;*.hdf5','Completed current camera scan steps'},'Choose one file per scan step','MultiSelect','on');
if isequal(files,0), return; end
if ischar(files), files={files}; end
hhg_analysis_load_hdf5(fig,cellfun(@(f)fullfile(p,f),files,'UniformOutput',false),'step_means');
end

function confirm_tc(fig)
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load a scan first.');
answer=questdlg('Do the loaded metadata setpoints represent mechanical TC positions in mm, one sample per delay?', ...
    'Verify TC axis','Yes','No','No');
if strcmp(answer,'Yes'), hhg_analysis_set_tc_axis(fig,h.delay); end
end

function toggle_background(fig,control)
assert(isappdata(fig,'hhg_background'),'HHG:Reference','Use as background on a reference frame first.');
enabled=~strcmp(get(control,'Checked'),'on');
setappdata(fig,'hhg_apply_background',enabled);
if enabled, set(control,'Checked','on'); else, set(control,'Checked','off'); end
label=getappdata(fig,'hhg_delay_label'); hhg_analysis_reset_context(fig,label); hhg_analysis_render(fig);
end

function load_hdf5(fig)
[f,p]=uigetfile({'*.h5;*.hdf5','Completed camera files'},'Read raw saved camera data');
if isequal(f,0), return; end
hhg_analysis_load_hdf5(fig,fullfile(p,f));
end

function import_axis(fig)
[f,p]=uigetfile('*.mat','MAT file containing calibrated xW');
if isequal(f,0), return; end
s=load(fullfile(p,f),'xW','result'); h=guidata(fig);
if ~isfield(s,'xW')&&isfield(s,'result')&&isfield(s.result,'calibration')
    s.xW=s.result.calibration.xW;
end
assert(h.loaded && isfield(s,'xW') && isvector(s.xW) && numel(s.xW)==h.width && ...
    isreal(s.xW) && all(isfinite(s.xW)) && (all(diff(s.xW)>0)||all(diff(s.xW)<0)), ...
    'HHG:Axis','xW must be a finite monotonic vector matching the loaded spectral width.');
setappdata(fig,'hhg_xW',double(s.xW(:)'));
setappdata(fig,'hhg_axis_source',fullfile(p,f));
end

function capture_tc(fig)
h=guidata(fig); assert(h.loaded,'HHG:NoData','Load a coherent-only image first.');
reference=struct('pixels',hhg_analysis_read(fig,h.nsel,1),'filename',h.filename);
setappdata(fig,'hhg_tc_reference',reference);
fprintf('TC coherent-only reference frozen: %s, frame %d. Reload the scan next.\n',h.filename,h.nsel);
end

function roi_fourier(fig)
h=guidata(fig); assert(h.loaded&&h.roi,'HHG:ROI','Select an ROI first.');
result=hhg_analysis_current_fourier(fig,'selected');
setappdata(fig,'hhg_last_result',result);
end

function save_result(fig)
assert(isappdata(fig,'hhg_last_result'),'HHG:NoResult','Run a workflow first.');
[f,p]=uiputfile('*.mat','Save derived result separately');
if isequal(f,0), return; end
path=fullfile(p,f); assert(~isfile(path),'HHG:Exists','Choose a new file; results never overwrite an existing file.');
result=getappdata(fig,'hhg_last_result'); %#ok<NASGU>
xW=getappdata(fig,'hhg_xW'); %#ok<NASGU>
save(path,'result','xW','-v7.3');
end

function review_sources(root)
files={'CovarianceFromGUIdata.m','TConlyAnalysisFromGUIdata.m', ...
    'wAxis_Calibration.m','w_axis_autoCalib_smoothed.m','normalizeData.m','normalizeDataWRT.m'};
[idx,ok]=listdlg('ListString',files,'SelectionMode','single','PromptString','Review original script / sections');
if ok, edit(fullfile(root,'reference',files{idx})); end
end

function safe(callback)
try, callback(); catch e
    disp(getReport(e,'extended','hyperlinks','off'));
    errordlg(e.message,'HHG analysis','non-modal');
end
end

function s=onoff(value)
if value, s='on'; else, s='off'; end
end
