function hhg_analysis_validate_fft_axis(delay,n)
%HHG_ANALYSIS_VALIDATE_FFT_AXIS Validate without publishing or reducing pixels.
assert(n>=2&&numel(delay)==n&&all(isfinite(delay)),'HHG:FFT','Fourier analysis requires one finite delay per frame and at least two frames.');
steps=diff(double(delay));
assert(steps(1)~=0&&all(abs(steps-steps(1))<=max(1e-10,abs(steps(1))*1e-6)), ...
    'HHG:FFT','Fourier analysis requires uniform nonzero delay spacing; input frames are never reordered or interpolated.');
end
