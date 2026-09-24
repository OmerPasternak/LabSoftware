% Analyze all completed camera scan files from one scan step in a folder.
% Set inputFolder before running, or use the repository's data folder.
% /images is [frame,y,x] in Python and [x,y,frame] in MATLAB. Values are ADU.
%
% g2Pixel(x,y) = <I(x,y)^2> / <I(x,y)>^2 across all frames.
% For each frame, J(x) = sum_y I(x,y) over yRange. pearsonMatrix is the
% Pearson correlation between J(x1) and J(x2) across frames. g2X is the
% intensity moment <J(x)^2>/<J(x)>^2, separate from Pearson's diagonal.
% These are zero-frame-lag camera ADU statistics, not g2(tau) or photon
% coincidences. Zero-mean g2 and zero-variance Pearson values return NaN.

if ~exist('inputFolder', 'var') || isempty(inputFolder) || isequal(inputFolder, 0)
    inputFolder = fullfile(fileparts(mfilename('fullpath')), '..', 'data');
end
if ~exist('yRange', 'var') || isempty(yRange)
    yRange = [];  % [] sums all y pixels; or [first last] in local ROI pixels
end
if ~exist('binWidth', 'var') || isempty(binWidth)
    binWidth = 1;  % x pixels per matrix bin; 1 gives a pixel-by-pixel matrix
end
if ~exist('backgroundADU', 'var') || isempty(backgroundADU)
    backgroundADU = 0;  % scalar dark offset in ADU, clipped at zero
end
if ~exist('batchFrames', 'var') || isempty(batchFrames)
    batchFrames = 32;  % cap; an adaptive memory limit may make batches smaller
end
clear g2Matrix  % remove a stale result from older versions of this script

inputFolder = char(inputFolder);
if ~isfolder(inputFolder)
    error('inputFolder must be an existing folder.');
end
if ~isscalar(binWidth) || ~isfinite(binWidth) || binWidth < 1 || binWidth ~= fix(binWidth)
    error('binWidth must be a positive integer in pixels.');
end
if ~isscalar(backgroundADU) || ~isfinite(backgroundADU) || backgroundADU < 0
    error('backgroundADU must be a nonnegative scalar in ADU.');
end
if ~isscalar(batchFrames) || ~isfinite(batchFrames) || ...
        batchFrames < 1 || batchFrames ~= fix(batchFrames)
    error('batchFrames must be a positive integer.');
end

entries = dir(fullfile(inputFolder, '*.h5'));
if isempty(entries)
    error('No completed .h5 scan files were found in %s.', inputFolder);
end
filesAnalyzed = sort(fullfile({entries.folder}, {entries.name}));

reference = [];
totalFrames = 0;
framesPerFile = zeros(1, numel(filesAnalyzed));
for fileIndex = 1:numel(filesAnalyzed)
    file = filesAnalyzed{fileIndex};
    info = h5info(file, '/images');
    if numel(info.Dataspace.Size) ~= 3
        error('%s: /images must have three dimensions.', file);
    end
    shape = double(info.Dataspace.Size);
    if any(shape < 1)
        error('%s: /images has an empty dimension.', file);
    end
    complete = h5readatt(file, '/', 'complete');
    if iscell(complete)
        complete = complete{1};  % h5py boolean can appear as {'TRUE'}
    end
    if ischar(complete) || isstring(complete)
        complete = strcmpi(complete, 'TRUE');
    end
    if ~isscalar(complete) || ~logical(complete)
        error('%s: scan file is marked incomplete.', file);
    end
    if double(h5readatt(file, '/', 'frames_written')) ~= shape(3)
        error('%s: frames_written does not match /images.', file);
    end

    % Require a common scan step, setpoint, image ROI, and exposure.
    metadata = {double(h5readatt(file, '/', 'scan_step_index')), ...
        char(h5readatt(file, '/', 'scan_parameter_name')), ...
        double(h5readatt(file, '/', 'scan_parameter_value')), ...
        double(h5readatt(file, '/', 'roi_bounds')), ...
        double(h5readatt(file, '/', 'exposure_time_s'))};
    if isempty(reference)
        reference = metadata;
        nx = shape(1);
        ny = shape(2);
        if isempty(yRange)
            yRange = [1 ny];
        end
        if numel(yRange) ~= 2 || any(~isfinite(yRange)) || ...
                any(yRange ~= fix(yRange)) || yRange(1) < 1 || ...
                yRange(2) > ny || yRange(1) > yRange(2)
            error('yRange must be [first last] within 1:%d.', ny);
        end
    elseif any(shape(1:2) ~= [nx ny]) || ~isequal(metadata, reference)
        error('%s: image size or scan-step metadata differs from the first file.', file);
    end
    totalFrames = totalFrames + shape(3);
    framesPerFile(fileIndex) = shape(3);
end
if totalFrames < 2
    error('At least two frames are required across the folder.');
end

nBins = ceil(nx / binWidth);
binIndex = ceil((1:nx)' / binWidth);
binCounts = accumarray(binIndex, 1, [nBins 1]);
xPixels = accumarray(binIndex, (1:nx)', [nBins 1]) ./ binCounts;
sumPixel = zeros(nx, ny);
sumPixelSquared = zeros(nx, ny);
sumX = zeros(nBins, 1);
sumXProducts = zeros(nBins, nBins);
% Bound the double image block to about 64 MiB, even for full sensor frames.
framesPerBatch = min(batchFrames, max(1, floor(64 * 1024^2 / (8 * nx * ny))));

for fileIndex = 1:numel(filesAnalyzed)
    file = filesAnalyzed{fileIndex};
    for firstFrame = 1:framesPerBatch:framesPerFile(fileIndex)
        count = min(framesPerBatch, framesPerFile(fileIndex) - firstFrame + 1);
        raw = h5read(file, '/images', [1 1 firstFrame], [nx ny count]);
        frame = reshape(double(raw), nx, ny, count);
        if backgroundADU > 0
            frame = max(frame - backgroundADU, 0);
        end
        clear raw;
        sumPixel = sumPixel + sum(frame, 3);
        sumPixelSquared = sumPixelSquared + sum(frame .* frame, 3);

        xProfiles = reshape(sum(frame(:, yRange(1):yRange(2), :), 2), nx, count);
        if binWidth == 1
            xIntensity = xProfiles;
        else
            padded = zeros(nBins * binWidth, count);
            padded(1:nx, :) = xProfiles;
            xIntensity = reshape(sum(reshape(padded, binWidth, nBins, count), 1), nBins, count) ./ binCounts;
        end
        sumX = sumX + sum(xIntensity, 2);
        % BLAS matrix multiplication replaces one x-x outer product per frame.
        sumXProducts = sumXProducts + xIntensity * xIntensity';
    end
end

meanPixelIntensity = sumPixel / totalFrames;
meanPixelSquared = sumPixelSquared / totalFrames;
g2Pixel = nan(nx, ny);
validPixel = meanPixelIntensity > 0;
g2Pixel(validPixel) = meanPixelSquared(validPixel) ./ ...
    (meanPixelIntensity(validPixel) .^ 2);

meanXIntensity = sumX / totalFrames;
meanXProducts = sumXProducts / totalFrames;
g2X = nan(nBins, 1);
validMean = meanXIntensity > 0;
diagonalProducts = diag(meanXProducts);
g2X(validMean) = diagonalProducts(validMean) ./ (meanXIntensity(validMean) .^ 2);

covariance = meanXProducts - meanXIntensity * meanXIntensity';
variance = max(diag(covariance), 0);
stdX = sqrt(variance);
stdProducts = stdX * stdX';
pearsonMatrix = nan(nBins, nBins);
validVariance = stdProducts > 0;
pearsonMatrix(validVariance) = covariance(validVariance) ./ stdProducts(validVariance);
pearsonMatrix(validVariance) = max(-1, min(1, pearsonMatrix(validVariance)));  % round-off guard
diagonalIndices = find(stdX > 0);
pearsonMatrix(sub2ind([nBins nBins], diagonalIndices, diagonalIndices)) = 1;

figure('Name', 'Camera scan g2(0) and x-x Pearson correlations');
tiledlayout(1, 3);
nexttile;
imagesc(1:nx, 1:ny, g2Pixel');
axis image;
axis xy;
colorbar;
xlabel('x pixel (local ROI index)');
ylabel('y pixel (local ROI index)');
title('Pixel-wise g^{(2)}(0)');
nexttile;
plot(xPixels, g2X, '.-');
xlabel('x pixel (local ROI index)');
ylabel('g^{(2)}(0) of summed y intensity');
grid on;
nexttile;
imagesc(xPixels, xPixels, pearsonMatrix);
axis image;
axis xy;
colorbar;
xlabel('x pixel (local ROI index)');
ylabel('x pixel (local ROI index)');
title('x-x Pearson correlation');

fprintf('Analyzed %d files and %d frames from scan step %d.\n', ...
    numel(filesAnalyzed), totalFrames, reference{1});
fprintf('Read up to %d frames per batch; x-x matrix is %d by %d.\n', ...
    framesPerBatch, nBins, nBins);
fprintf('Results: g2Pixel, g2X, pearsonMatrix, xPixels, meanPixelIntensity, meanXIntensity.\n');
