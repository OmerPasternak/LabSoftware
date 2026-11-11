function hhg_analysis_write_processing(path,processing)
%HHG_ANALYSIS_WRITE_PROCESSING Attach correction/sample provenance to derived exports.
sidecar=[path '.processing.json'];
assert(~isfile(sidecar),'HHG:Exists','Processing sidecar already exists: %s',sidecar);
fid=fopen(sidecar,'wt'); assert(fid~=-1,'HHG:Export','Cannot create processing sidecar.');
cleanup=onCleanup(@()fclose(fid));
text=jsonencode(processing); written=fwrite(fid,text,'char');
assert(written==numel(text),'HHG:Export','Processing sidecar write was incomplete.');
end
