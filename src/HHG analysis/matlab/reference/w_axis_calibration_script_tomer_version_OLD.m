%%path for image file
%{
% full_path = 'Z:\Top Lab\HHG 2024\Sun, Jan 14, 2024\20240114_130819__no_Ar27_33_10_1.25_1.7_Al_WithNe0.92A_Iris22Slit0.15_2500_300.pic';
full_path = 'Z:\Top Lab\HHG 2024\Sun, Jan 14, 2024\20240114_164032__Ar15_Ar31_33_10_1.2_1.7_Al_toUse_cellSpec_WP45.5_90degPerp_Ne1.04A_2500_300.pic';

width = 2500;
height = 300;
data = openSingleImage(full_path,[width,height],0);
dataflp = flip(data, 2); % Because the Y axis starts from the bottom and the picture and from the top
roidata = flip(dataflp(1:2500,80:230), 2);
rawData = flip(permute(roidata, [2 1]),1);
rawDataYAvg = squeeze(mean(rawData));

%Plot the loaded signal and it's spatial average
figure()
subplot(2,1,1)
imagesc(rawData)
xlabel('w axis, not calibrated')
ylabel('spatial')
title('Full Spectrum (not averaged)')

subplot(2,1,2)
plot(rawDataYAvg)
xlabel('w axis, not calibrated')
ylabel('intensity')
title('Full Spectrum (spatial averaged)')



%}


%% Suggest pick location 
% Set minimum distance between peaks

spacePix = 60:80;

idxNum = 7; % Number of indices to calibrate by


%rawDataYAvg =  mean(rawData.scans{179}(35:65,:,:),[1,3]);
%rawDataYAvg =  meanPlot(:,50);
rawDataYAvg = mean(data(:,spacePix,:),[2,3]);

figure;
plot(rawDataYAvg)

minDist = 100;

% Find peaks and their locations
[peaks, locs] = findpeaks(rawDataYAvg, 'MinPeakDistance', minDist);

% Filter out peaks with index < 550, to get only H11-21
validIdx = locs >= 400;
peaks = peaks(validIdx);
locs = locs(validIdx);

% Sort peaks in descending order and get top 6
[~, sortIdx] = sort(peaks, 'descend');
top6Idx = locs(sortIdx(1:min(idxNum, numel(locs))));

% Sort top 6 indices from high to low
sortedTop6Idx = sort(top6Idx, 'descend');

% Plot the suggestion
hold on;
% Plot thin vertical lines at peak positions
yLimits = ylim;  % Get y-axis limits for full-height lines
for i = 1:length(sortedTop6Idx)
xline(sortedTop6Idx(i), 'r--', 'LineWidth', 0.5);  % Thin red dashed line
end
%% Continue for calibration

% we need 3 things:
% oddH = indices of centers of harmonics
% oddHnum = harmonic numbers of harmonics
% plotSpec = the actual spectrum after spatial averaging

oddH = sortedTop6Idx;

% If the suggestion was not good, you can choose the peaks manually
% oddH = [2218,1721,1344,1043,795,591];

% The harnomic we fit to
oddHNum = 11:2:11+2*(idxNum-1);

% define plotSpec here
plotSpec = rawDataYAvg;

%% Perform the fit automatically

xForFit = oddH;
yForFit = oddHNum;

% Define the fit type with custom equation
fitEq = fittype(@(a, b, c, x) 1./(b.*((x./1000)./sqrt(1 - (x./1000).^2 .* a) + c)), ...
    'independent', 'x', 'coefficients', {'a', 'b', 'c'});

% Set options for the fitting, including start points to avoid divergence
opts = fitoptions(fitEq);
opts.StartPoint = [0.0347, 0.22, 0.01];  % a, b, c
opts.Lower = [0, 0, -Inf];              % Optional: constrain to avoid invalid sqrt
opts.Upper = [Inf, Inf, Inf];

% Perform the fit
[fitResult, gof] = fit(xForFit(:), yForFit(:), fitEq, opts);

% Extract the coefficients
a = fitResult.a;
b = fitResult.b;
c = fitResult.c;

% Display results
figure()
plot(fitResult, xForFit, yForFit);
fprintf('Fitted parameters:\na = %.6f\nb = %.6f\nc = %.6f\n', a, b, c);
title('Automated fit')

% IF THE FIT IS BAD, and you want to do it manually:
% use custom fit: 1/(b*((x/1000)/sqrt(1-(x/1000)^2*a)+c))
% (this is the equation for a hyperbolic grating like we have)
% Set a to 0.0347 in fit and b to 0.22 to avoid divergence

%% Create axis and harmonic axises

XtoWIR = @(xX)  1./( b    * ( xX./1000 ./ ( sqrt(1-(xX/1000).^2*a) ) +c )   ); 
xW = XtoWIR(1:length(plotSpec));

% xW_perH = cell(6, 1);
% % Loop to extract slices
% for i = 1:6
% startIdx = fullData.d.h.roiX(i);
% endIdx = startIdx + fullData.d.h.roiWidth(i) - 1;
% xW_perH{i} = xW(startIdx:endIdx);
% end
% 
% figure
% plot(xW,rawDataYAvg)
% title('Full Spectrum (spatial averaged), with CALIBRATED \omega axis (\omega_{IR} units)')
% 

figure()
plot(xW,rawDataYAvg)
xlabel('w axis, calibrated')
ylabel('Intensity')
title('Full Spectrum (not averaged)')
