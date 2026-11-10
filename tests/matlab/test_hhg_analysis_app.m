function test_hhg_analysis_app(folder,workflow)
% Exercise the actual desktop-free interactive entrypoint and its lifetime.
addpath(folder); assert(~usejava('desktop'),'Desktop must remain absent.');
setappdata(groot,'hhg_app_checked',false);
t=timer('ExecutionMode','fixedSpacing','Period',0.2,'TimerFcn',@check);
cleanup=onCleanup(@()finish(t)); %#ok<NASGU>
start(t); hhg_analysis_app('',workflow);
stop(t); delete(t);
assert(getappdata(groot,'hhg_app_checked'),'App never reached its analysis interface.');
disp('APP_ONLY_OK');
    function check(~,~)
        figures=findall(groot,'Type','figure');
        for j=1:numel(figures)
            if isappdata(figures(j),'hhg_entry_workflow')
                assert(strcmp(getappdata(figures(j),'hhg_entry_workflow'),workflow));
                assert(~usejava('desktop')); setappdata(groot,'hhg_app_checked',true);
                close(figures(j));
            end
        end
    end
end
function finish(t)
if isvalid(t), stop(t); delete(t); end
close('all','force');
end
