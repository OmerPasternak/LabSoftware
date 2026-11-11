function fig = hhg_analysis_app(inputPath,workflow,waitForClose)
%HHG_ANALYSIS_APP Run an interactive analysis interface without the desktop.
% The invisible COM host owns lifetime; CLI callers can wait for figures.
if nargin<1, inputPath=''; end
if nargin<2, workflow='show_scan'; end
if nargin<3, waitForClose=true; end
assert(ismember(workflow,{'show_scan','statistical','tcs','pearson'}),'HHG:Workflow','Unknown workflow.');
set(groot,'DefaultFigureVisible','on');
set(groot,'DefaultFigureWindowStyle','normal');
try
    fig=hhg_analysis_start();
    set(findall(fig,'Type','uimenu','Label','Review original scripts (sections; do not run blindly)'),'Visible','off');
    set(findall(fig,'Type','uimenu','Label','Feature guide / missing dependencies'),'Visible','off');
    setappdata(fig,'hhg_entry_workflow',workflow);
    if ~isempty(inputPath), hhg_analysis_load_hdf5(fig,inputPath); end
    set(fig,'Visible','on');
    if strcmp(workflow,'pearson')&&~isempty(inputPath)
        hhg_analysis_workflow(fig,'pearson');
    end
    if ~waitForClose, return; end
    % Wait for every analysis window, including plots left open after the
    % analyzer closes. Never expose the desktop/editor as the application.
    while true
        figures=findall(groot,'Type','figure');
        if isempty(figures), break; end
        waitfor(figures(1));
    end
catch e
    disp(getReport(e,'extended','hyperlinks','off'));
    dialog=errordlg(e.message,'Analysis error','modal');
    waitfor(dialog);
    rethrow(e);
end
end
