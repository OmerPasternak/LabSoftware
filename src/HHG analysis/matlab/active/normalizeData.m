function [normData] = normalizeData(data,toRun)
    %Normalized data to values between 0 and 1
    
    if nargin ==1
        toRun = true;
    end
    
    if toRun
    normData = ( data-min(data,[],'all') ) /( max(data,[],'all') -min(data,[],'all')   );
    else
        normData = data;
    end
end

