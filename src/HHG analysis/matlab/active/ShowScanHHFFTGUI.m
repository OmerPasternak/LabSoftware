function results = ShowScanHHFFTGUI()
%SHOWSCANHHFFTGUI Recreate harmonic-mask per-pixel Fourier views.
% The source exporter supplies [y,x,delay] per harmonic. No harmonic
% aggregation is inferred from the absent historical helper.
prefix=evalin('base','loadname');
assert(isvarname(prefix),'HHG:Prefix','Use a valid MATLAB variable prefix.');
orders=evalin('base',[prefix '_Hnumbase']);
delay=evalin('base',[prefix '_piezoaxis']);
results=cell(size(orders));
for k=1:numel(orders)
    variable=matlab.lang.makeValidName([prefix '_H' num2str(orders(k)) 'data']);
    assert(isvarname(variable),'HHG:Prefix','Use a valid MATLAB variable prefix and harmonic label.');
    exported=evalin('base',variable);
    results{k}=hhg_analysis_pixel_fft(permute(exported,[2 1 3]),delay,variable);
end
assignin('base','hhg_harmonic_fft_results',results);
end
