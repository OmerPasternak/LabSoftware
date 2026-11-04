function value = hhg_analysis_bin_size(text)
%HHG_ANALYSIS_BIN_SIZE Parse the original even-or-one bin rule without eval.
value=str2double(text);
assert(isscalar(value)&&isfinite(value)&&value>=1&&value==fix(value),'HHG:Bin','Enter a positive integer bin size.');
if value>1&&mod(value,2)~=0, value=4; end
end
