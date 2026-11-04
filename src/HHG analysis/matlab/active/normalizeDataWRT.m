function [normDataWRT] = normalizeDataWRT(data,dataWRT)
%Normalized data to values between min(dataWRT) and max(dataWRT)
normData = ( data-min(data,[],'all') ) /( max(data,[],'all') -min(data,[],'all')   );
normDataWRT = normData* (   max(dataWRT,[],'all') -min(dataWRT,[],'all')  )    +  min(dataWRT,[],'all')   ;
end

