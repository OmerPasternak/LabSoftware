function path = hhg_analysis_export_mat(fig,path)
%HHG_ANALYSIS_EXPORT_MAT Write all source samples to new v7.3 MAT in batches.
% Partial output remains labeled incomplete on failure. Raw source files are
% read-only. Step-means mode is explicitly identified as a derived dataset.
if nargin<2
    [f,p]=uiputfile('*.mat','Export full loaded source separately');
    if isequal(f,0), path=''; return; end
    path=fullfile(p,f);
end
assert(~isfile(path)&&~isfile([path '.partial.mat']),'HHG:Exists','Choose a new output filename.');
h=guidata(fig); source=[];
if isappdata(fig,'hhg_hdf5_source'), source=getappdata(fig,'hhg_hdf5_source'); end
partial=[path '.partial.mat']; output=matfile(partial,'Writable',true);
output.complete=false; output.source=source; output.delay=h.delay;
example=hhg_analysis_read(fig,1,1); output.data(h.width,h.height,h.flength)=cast(0,'like',example);
batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1);
    output.data(:,:,first:first+count-1)=hhg_analysis_read(fig,first,count);
    drawnow limitrate nocallbacks;
end
output.complete=true; clear output; movefile(partial,path);
end
