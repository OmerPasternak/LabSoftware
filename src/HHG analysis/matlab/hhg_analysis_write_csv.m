function path = hhg_analysis_write_csv(path,values,processing)
%HHG_ANALYSIS_WRITE_CSV Preserve legacy CSV values/precision without overwrite.
[folder,stem,extension]=fileparts(path); candidate=path; index=1;
while isfile(candidate)||isfile([candidate '.processing.json'])
    candidate=fullfile(folder,sprintf('%s_%d%s',stem,index,extension)); index=index+1;
end
path=candidate; dlmwrite(path,values);
if nargin>=3, hhg_analysis_write_processing(path,processing); end
fprintf('Saved CSV: %s\n',path);
end
