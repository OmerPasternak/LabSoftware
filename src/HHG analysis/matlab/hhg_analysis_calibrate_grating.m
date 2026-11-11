function result = hhg_analysis_calibrate_grating(data,preset,orders,showPlots)
%HHG_ANALYSIS_CALIBRATE_GRATING Consolidate the complete supplied fit variants.
% This fits the source equation with source start points and bounds, using
% MATLAB findpeaks and Curve Fitting Toolbox fit (no substitute optimizer).
if nargin<4, showPlots=true; end
assert(~isempty(which('findpeaks'))&&~isempty(which('fit')),'HHG:MissingHelper', ...
    'Grating calibration requires Signal Processing Toolbox findpeaks and Curve Fitting Toolbox fit.');
assert(size(data,1)==2500,'HHG:Calibration','The supplied calibration presets require 2500 spectral pixels.');
switch preset
    case 'smoothed_maxima'
        rows=40:70; smoothing=true; useFwhm=false;
    case 'old_unsmoothed_maxima'
        rows=60:80; smoothing=false; useFwhm=false;
    case 'fwhm_direct_fit'
        rows=40:100; smoothing=true; useFwhm=true;
    otherwise
        error('HHG:Calibration','Unknown calibration preset.');
end
assert(size(data,2)>=rows(end),'HHG:Calibration','Image has too few spatial pixels for this source preset.');
raw=mean(data(:,rows,:),[2 3]); spectrum=raw;
if smoothing
    a=movmean(raw,50); b=movmean(raw,100);
    spectrum(1:2500)=a(1:2500); spectrum(2500:end)=b(2500:end);
end
[peaks,locs]=findpeaks(spectrum,'MinPeakDistance',100);
valid=locs>=400; peaks=peaks(valid); locs=locs(valid);
[~,idx]=sort(peaks,'descend');
assert(numel(idx)>=numel(orders),'HHG:Calibration','Not enough detected peaks for the requested harmonic orders.');
locs=sort(locs(idx(1:numel(orders))),'descend');
detected=locs;
if useFwhm
    halfMax=(spectrum(locs)-spectrum(1))/2+spectrum(1);
    currentRight=2500;
    for k=1:numel(locs)
        left=find(spectrum(1:locs(k))<=halfMax(k),1,'last');
        if isempty(left), left=1; end
        right=find(spectrum(locs(k):currentRight)<=halfMax(k),1,'first')+locs(k)-1;
        if isempty(right), right=numel(spectrum); end
        currentRight=right; locs(k)=round((left+right)/2);
    end
end
fitEq=fittype(@(a,b,c,x)1./(b.*((x./1000)./sqrt(1-(x./1000).^2.*a)+c)), ...
    'independent','x','coefficients',{'a','b','c'});
options=fitoptions(fitEq); options.StartPoint=[0.0347 0.22 0.01];
options.Lower=[0 0 -Inf]; options.Upper=[Inf Inf Inf];
[fitted,gof]=fit(locs(:),orders(:),fitEq,options);
x=1:2500;
xW=1./(fitted.b*(x./1000./sqrt(1-(x/1000).^2*fitted.a)+fitted.c));
assert(isreal(xW)&&all(isfinite(xW))&&(all(diff(xW)>0)||all(diff(xW)<0)), ...
    'HHG:Calibration','Fitted harmonic axis is invalid; calibration was not accepted.');
result=struct('xW',xW,'preset',preset,'orders',orders,'detected_peaks',detected, ...
    'fit_locations',locs,'raw_spectrum',raw,'smoothed_spectrum',spectrum, ...
    'coefficients',[fitted.a fitted.b fitted.c],'gof',gof);
if showPlots
    figure('Name','Calibration peak review'); plot(raw); hold on; plot(spectrum);
    for k=1:numel(locs), xline(locs(k),'r--','LineWidth',0.5); end
    if useFwhm, set(gca,'XDir','reverse'); end
    figure('Name','Grating fit'); plot(fitted,locs,orders); title('Automated fit');
    figure('Name','Calibrated spectrum'); plot(xW,raw); hold on; plot(xW,spectrum);
    xlabel('w axis, calibrated'); ylabel('Intensity'); legend('Not smoothed','Smoothed');
end
end
