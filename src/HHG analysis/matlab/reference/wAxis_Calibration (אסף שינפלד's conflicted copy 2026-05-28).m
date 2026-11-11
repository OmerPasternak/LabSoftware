function [xW] = wAxis_Calibration(data, show_fig)
    % This function gets raw data (3D array) and performs w axis calibration
    % using the known harmonic peaks.
    % Input:
    %   data: 3D array of size (2500, <200, >3000), where the first dimension
    %         is the spectral axis, the second is the spatial axis (e.g., y
    %         axis on camera), and the third is the delay/repetition axis.
    %   show_fig: boolean, whether to show figures (default: false)
    % Output:
    %   xW: calibrated w axis (in omega_IR units). This is a vector of length 2500.
    %
%% Check inputs
    if nargin < 2
        show_fig = false;
    end
%% Check data dimensions
% Assume the first dimension is the spectral axis
% and the second dimension is the spatial axis (e.g., y-axis on camera).
% If exists a third dimension, it is the delay/repetition axis.
% Order data dimensions if needed - make sure the first dimension is 2500. the second should be less than 300.
% If the data is already in the correct order, skip this step.
if size(data, 1) ~= 2500
    if size(data, 2) == 2500
        data = permute(data, [2, 1, 3]); % Swap first and second dimensions
    elseif size(data, 3) == 2500
        data = permute(data, [3, 2, 1]); % Move third dimension to first
    else
        error('Data does not have a dimension of size 2500.');
    end
end

%% Suggest pick location 
% Set minimum distance between peaks
idxNum = 7; % Number of harmonics to choose
isMovMean = true; % Should spectrum be smoothed?
windowSize1 = 50; % window size for higher harmonics
windowSize2 = 100; % window size for lower harmonics
sepWind1Wind2 = 2500; % freq. index to switch windows
rawDataYAvgNoSmooth = mean(data(:,40:100,:),[2,3]); % spatial average over y axis and delay axis

movMean1 = movmean(rawDataYAvgNoSmooth,windowSize1);
movMean2 = movmean(rawDataYAvgNoSmooth,windowSize2);
rawDataYAvg(1:sepWind1Wind2) =movMean1(1:sepWind1Wind2);
rawDataYAvg(sepWind1Wind2:length(rawDataYAvg)) =movMean2(sepWind1Wind2:length(rawDataYAvg));

if ~isMovMean
    rawDataYAvg = rawDataYAvgNoSmooth;
end

if show_fig
    figure; hold on;
    plot(rawDataYAvgNoSmooth);
    plot(rawDataYAvg)
    title('Full Spectrum (spatial averaged), not smoothed (blue) and smoothed (orange)')
    xlabel('Pixel index')
    ylabel('Intensity (a.u.)')
    set(gca, "XDir","reverse");
end

minDist = 100;  % Minimum distance between peaks in indices
% Find peaks and their locations
%[peaks, locs] = findpeaks(rawDataYAvg, 'MinPeakDistance', minDist);

%% Locate peaks in the center of FWHM of each peak of rawDataYAvg
% find the peaks that are the maximums of each harmonic.
[peaksMax, locsMax] = findpeaks(rawDataYAvg, 'MinPeakDistance', minDist);

% Filter out peaks with index < 550, to get only H11-21
validIdx = locsMax >= 400;
peaksMax = peaksMax(validIdx);
locsMax = locsMax(validIdx);

% Sort peaks in descending order and get top 6
[~, sortIdx] = sort(peaksMax, 'descend');
top6Idx = locsMax(sortIdx(1:min(idxNum, numel(locsMax))));

% Sort top 6 indices from high to low
sortedTop6Idx = sort(top6Idx, 'descend');
locs = sortedTop6Idx;
peaks = rawDataYAvg(sortedTop6Idx);
% Plot the suggestion
if show_fig
    hold on;
    % Plot thin vertical lines at peak positions
    yLimits = ylim;  % Get y-axis limits for full-height lines
    for i = 1:length(peaks)
        xline(locs(i), 'b--', 'LineWidth', 0.5);  % Thin red dashed line
    end
end

% The center of the distribution is not the peak location, but the center of the FWHM
halfMax = ((peaks - rawDataYAvg(1)) / 2) + rawDataYAvg(1);
fwhmCenters = zeros(size(locs));
currentRightidx = 2500;
for i = 1:length(locs)
    peakIdx = locs(i);
    % Find left half-maximum
    leftIdx = find(rawDataYAvg(1:peakIdx) <= halfMax(i), 1, 'last');
    if isempty(leftIdx)
        leftIdx = 1; % If no left half-maximum found, set to start
    end
    %xline(leftIdx, 'c--', 'LineWidth', 1.5);  % Thin red dashed line %%%%%%%%%%%%%%%%%%%%%
    % Find right half-maximum
    rightIdx = find(rawDataYAvg(peakIdx:currentRightidx) <= halfMax(i), 1, 'first') + peakIdx - 1;
    if isempty(rightIdx)
        rightIdx = length(rawDataYAvg); % If no right half-maximum found, set to end
    end
    currentRightidx = rightIdx;
    %xline(rightIdx, 'c--', 'LineWidth', 1.5);  % Thin red dashed line %%%%%%%%%%%%%%%%%%%%
    % Calculate center of FWHM
    fwhmCenters(i) = (leftIdx + rightIdx) / 2;
end
% Plot the suggestion
if show_fig
    hold on;
    % Plot thin vertical lines at peak positions
    yLimits = ylim;  % Get y-axis limits for full-height lines
    for i = 1:length(fwhmCenters)
        xline(fwhmCenters(i), 'g--', 'LineWidth', 1.5);  % Thin red dashed line
    end
end
locs = round(fwhmCenters); % Update locs to be the FWHM centers
peaks = rawDataYAvg(locs); % Update peaks to be the values at the new locs

% Plot the suggestion
if show_fig
    hold on;
    % Plot thin vertical lines at peak positions
    yLimits = ylim;  % Get y-axis limits for full-height lines
    for i = 1:length(peaks)
        xline(locs(i), 'r--', 'LineWidth', 1.5);  % Thin red dashed line
    end
end

%% Continue for calibration
% we need 3 things:
% oddH = indices of centers of harmonics
% oddHnum = harmonic numbers of harmonics
% plotSpec = the actual spectrum after spatial averaging
% oddH = sortedTop6Idx; % changed by assaf
oddH = locs;
% If the suggestion was not good, you can choose the peaks manually
% oddH = [2218,1721,1344,1043,795,591];

% The harmonic we fit to
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
if show_fig
    figure()
    plot(fitResult, xForFit, yForFit);
    %fprintf('Fitted parameters:\na = %.6f\nb = %.6f\nc = %.6f\n', a, b, c);
    title('Automated fit')
end

% IF THE FIT IS BAD, and you want to do it manually:
% use custom fit: 1/(b*((x/1000)/sqrt(1-(x/1000)^2*a)+c))
% (this is the equation for a hyperbolic grating like we have)
% Set a to 0.0347 in fit and b to 0.22 to avoid divergence

%% Create axis and harmonic axises
XtoWIR = @(xX)  1./( b    * ( xX./1000 ./ ( sqrt(1-(xX/1000).^2*a) ) +c )   ); 
xW = XtoWIR(1:length(plotSpec));

% Show final result
if show_fig
    figure();hold on
    plot(xW,rawDataYAvgNoSmooth)
    plot(xW,rawDataYAvg)
    xlabel('w axis, calibrated')
    ylabel('Intensity')
    title('Full Spectrum (not averaged)')
    legend('Not smoothed','Smoothed')
end
