function result = ShowScanROIFFTGUI()
%SHOWSCANROIFFTGUI Recreate the missing per-pixel ROI Fourier entry point.
% Integrated Fourier aggregation is deliberately inactive, not guessed.
data=evalin('base','roidata'); delay=evalin('base','delay');
label=evalin('base','filename');
result=hhg_analysis_pixel_fft(data,delay,['ROI ' label]);
assignin('base','hhg_roi_fft_result',result);
end
