function pixels=hhg_analysis_read_legacy_bin(path,width,height)
%HHG_ANALYSIS_READ_LEGACY_BIN Validate complete big-endian uint32 input.
% Preserve historical uint32=>uint16 conversion and [x,y] orientation.
fid=fopen(path,'rb'); assert(fid~=-1,'HHG:LegacyBIN','Cannot open legacy BIN: %s',path);
cleanup=onCleanup(@()fclose(fid));
[pixels,count]=fread(fid,[width height],'uint32=>uint16',0,'b');
assert(count==width*height&&isequal(size(pixels),[width height]), ...
    'HHG:LegacyBIN','Truncated/inconsistent legacy BIN: expected %d samples, read %d.',width*height,count);
assert(isempty(fread(fid,1,'*uint8')),'HHG:LegacyBIN','Legacy BIN has trailing/inconsistent bytes.');
end
