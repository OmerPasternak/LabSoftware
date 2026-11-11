function result = ShowScanPxFFTGUI()
%SHOWSCANPXFFTGUI Recreate the missing per-pixel Fourier workspace contract.
% Source ShowScanGUI supplies uint16 binned data, retaining its saturation.
data=evalin('base','bindata'); delay=evalin('base','delay');
label=evalin('base','filename');
result=hhg_analysis_pixel_fft(data,delay,label);
assignin('base','hhg_pixel_fft_result',result);
end
