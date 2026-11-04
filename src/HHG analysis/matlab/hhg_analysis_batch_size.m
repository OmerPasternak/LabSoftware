function count = hhg_analysis_batch_size(width,height)
%HHG_ANALYSIS_BATCH_SIZE At most eight raw frames / ~32 MiB per read.
count=max(1,min(8,floor(32*1024^2/(2*double(width)*double(height)))));
end
