function hhg_analysis_current_action(fig,control,event) %#ok<INUSD>
%HHG_ANALYSIS_CURRENT_ACTION Use current HDF5 adapters for original controls.
h=guidata(fig); tag=get(control,'Tag');
switch tag
    case 'slider1'
        h.nsel=round(get(control,'Value')); guidata(fig,h); hhg_analysis_render(fig);
    case {'radiobutton1','radiobutton2'}
        h.scale=strcmp(tag,'radiobutton2');
        set(h.radiobutton1,'Value',~h.scale); set(h.radiobutton2,'Value',h.scale);
        guidata(fig,h); hhg_analysis_render(fig);
    case 'pushbutton3'
        h.roi=0; guidata(fig,h);
        if isappdata(fig,'hhg_trace'), rmappdata(fig,'hhg_trace'); end
        hhg_analysis_render(fig);
    case 'pushbutton14'
        [f,p]=uiputfile('*.png','Export current analysis figure');
        if ~isequal(f,0), assert(~isfile(fullfile(p,f)),'HHG:Exists','Choose a new output filename.'); exportapp(fig,fullfile(p,f)); hhg_analysis_write_processing(fullfile(p,f),hhg_analysis_processing(fig)); end
    case 'pushbutton15'
        source=getappdata(fig,'hhg_hdf5_source');
        frame=hhg_analysis_read(fig,h.nsel,1); processing=hhg_analysis_processing(fig);
        assignin('base','hhg_source',source);
        assignin('base','hhg_current_frame',frame);
        assignin('base','hhg_processing',processing);
        assignin('base','filename',h.filename); assignin('base','delay',h.delay);
        fprintf('Exported hhg_source and hhg_current_frame. Full pixels remain on disk; use batched MAT export for the entire source.\n');
    case 'pushbutton19'
        source=getappdata(fig,'hhg_hdf5_source');
        % Capture the underlying raw frame, never a recursively corrected one.
        enabled=isappdata(fig,'hhg_apply_background')&&getappdata(fig,'hhg_apply_background');
        restore=onCleanup(@()setappdata(fig,'hhg_apply_background',enabled));
        setappdata(fig,'hhg_apply_background',false);
        index=1; if strcmp(source.mode,'step_means'), index=h.nsel; end
        reference=struct('pixels',hhg_analysis_read(fig,h.nsel,1),'source',source.paths{index}, ...
            'frame',h.nsel,'scale',.95,'sample_mode',source.mode,'aggregation','single raw frame');
        if strcmp(source.mode,'step_means')
            reference.aggregation='mean of all raw frames in the selected step';
            reference.frame_range=[1 source.frame_counts(index)];
        end
        clear restore;
        setappdata(fig,'hhg_background',reference); hhg_analysis_invalidate_derived(fig);
        fprintf('Background captured. Enable the explicit 0.95x background menu option to use it for derived reads.\n');
    case 'pushbutton20'
        if isappdata(fig,'hhg_background'), rmappdata(fig,'hhg_background'); end
        setappdata(fig,'hhg_apply_background',false);
        hhg_analysis_reset_context(fig,getappdata(fig,'hhg_delay_label')); hhg_analysis_render(fig);
    case 'pushbutton17'
        hhg_analysis_current_fourier(fig,'full');
    case 'pushbutton23'
        hhg_analysis_select_roi(fig); hhg_analysis_current_fourier(fig,'selected');
    case 'pushbutton24'
        assert(h.width>=966&&h.height>=118,'HHG:ROI','The source fixed ROI [374 77 593 42] does not fit this acquisition geometry. Select an explicit ROI instead.');
        hhg_analysis_select_roi(fig,[374 77 593 42]); hhg_analysis_current_fourier(fig,'selected');
    case {'pushbutton21','pushbutton25','pushbutton26','pushbutton27'}
        hhg_analysis_current_masks(fig,tag);
    case 'checkbox1'
        if get(control,'Value'), zoom(fig,'on'); else, zoom(fig,'off'); end
    case 'checkbox2'
        if get(control,'Value'), datacursormode(fig,'on'); else, datacursormode(fig,'off'); end
    otherwise
        error('HHG:Action','No current-format adapter for %s.',tag);
end
end
