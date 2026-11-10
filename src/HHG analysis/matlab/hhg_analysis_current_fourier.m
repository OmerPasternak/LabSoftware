function result = hhg_analysis_current_fourier(fig,mode,makePlots)
%HHG_ANALYSIS_CURRENT_FOURIER Batch-reduce a selected source before pixel FFT.
% Original full-image binning saturates in uint16; retain this derived rule.
if nargin<3, makePlots=true; end
h=guidata(fig); bin=hhg_analysis_bin_size(get(h.edit1,'String'));
if strcmp(mode,'selected')
    assert(h.roi,'HHG:ROI','Select an ROI first.');
    bounds=[h.roix h.roiy h.roiwidth h.roiheight];
else, bounds=[1 1 h.width h.height]; end
w=floor(bounds(3)/bin); height=floor(bounds(4)/bin);
assert(w>=1&&height>=1,'HHG:Bin','Bin size exceeds the selected image dimensions.');
assert(8*double(w)*height*h.flength*4<=1024^3,'HHG:Memory','Pixel FFT exceeds 1 GiB. Increase the explicit bin size or select a smaller ROI.');
binned=zeros(w,height,h.flength,'uint16'); batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1); data=hhg_analysis_read(fig,first,count);
    if strcmp(mode,'selected')
        data=flip(data,2); data=data(bounds(1):bounds(1)+bounds(3)-1,bounds(2):bounds(2)+bounds(4)-1,:);
        data=flip(data,2);
    end
    data=double(data(1:w*bin,1:height*bin,:));
    values=reshape(data,bin,w,bin,height,count);
    values=sum(sum(values,1),3);
    binned(:,:,first:first+count-1)=uint16(reshape(values,w,height,count));
end
result=hhg_analysis_pixel_fft(binned,h.delay,h.filename,makePlots);
result.bin_size=bin; result.bounds=bounds; result.binning='source uint16 saturation; incomplete edge bins omitted';
if isappdata(fig,'hhg_hdf5_source'), result.source=getappdata(fig,'hhg_hdf5_source'); end
result.processing=hhg_analysis_processing(fig); result.processing.binning=result.binning;
result.processing.bin_size=bin; result.processing.bounds=bounds;
setappdata(fig,'hhg_last_result',result);
end
