function hhg_analysis_load_hdf5(fig,paths,mode)
%HHG_ANALYSIS_LOAD_HDF5 Attach current camera HDF5 without loading its stack.
% Frame mode is one fixed scan step. Explicit step_means mode keeps steps
% separate and represents each by the mean of all its recorded repetitions.
if nargin<3, mode='frames'; end
source=hhg_analysis_inspect_hdf5(paths,mode);
h=guidata(fig);
h.width=source.width; h.height=source.height;
if strcmp(mode,'frames')
    h.flength=source.frame_counts(1); h.delay=1:h.flength; axisLabel='Frame index (one scan step)';
else
    h.flength=numel(source.paths); h.delay=source.setpoints; axisLabel=source.parameter_name;
end
h.data=[]; h.nsel=1; h.loaded=2; h.filename=strjoin(source.paths,'; ');
h.x=(1:h.width)'; h.y=h.height:-1:1;
h.roi=0; h.roix=1; h.roiy=1; h.roiwidth=min(50,h.width); h.roiheight=min(50,h.height);
h.bg=0; h.minval=0.1; h.maxval=1;
guidata(fig,h); setappdata(fig,'hhg_hdf5_source',source);
hhg_analysis_reset_context(fig,axisLabel);
frame=hhg_analysis_read(fig,1,1);
h.minval=double(min(frame,[],'all'))+31; h.maxval=double(max(frame,[],'all')); guidata(fig,h);
set(h.slider1,'Min',1,'Max',max(2,h.flength),'Value',1, ...
    'SliderStep',[1/max(1,h.flength) 1/max(1,h.flength)]);
if h.flength>1, set(h.slider1,'Enable','on'); else, set(h.slider1,'Enable','off'); end
set([h.pushbutton2 h.pushbutton3 h.radiobutton1 h.radiobutton2],'Enable','on');
set(h.pushbutton15,'String','Export source + frame');
[~,name,extension]=fileparts(source.paths{1});
if strcmp(mode,'frames'), label=[name extension]; else, label=sprintf('%d HDF5 scan steps',h.flength); end
set(h.text2,'String',label,'TooltipString',h.filename);
set(h.text15,'String','HDF5 v2.0','TooltipString','Raw pixels stay on disk; completed schema-2.0 input, read in bounded batches.');
hhg_analysis_render(fig);
end
