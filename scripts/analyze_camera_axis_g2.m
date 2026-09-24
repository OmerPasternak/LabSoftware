% Analyze all completed camera scan files from one scan step in a folder.
% Select a folder when prompted, or set inputFolder before running.
% /images is [frame,y,x] in Python and [x,y,frame] in MATLAB. Values are ADU.
%
% g2Pixel(x,y) = <I(x,y)^2> / <I(x,y)>^2 across all frames.
% For each frame, J(x) = sum_y I(x,y) over yRange. The x-x matrix is
% g2Matrix(i,j) = <J(i)J(j)> / (<J(i)><J(j)>). g2X is its diagonal.
% These are zero-frame-lag intensity correlations, not g2(tau) or photon
% coincidence correlations. Zero-mean locations return NaN.

if ~exist('inputFolder', 'var') || isempty(inputFolder)
    inputFolder = uigetdir(pwd, 'Select folder with one scan step');
    if isequal(inputFolder, 0)
        return;
    end
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

entries = dir(fullfile(inputFolder, '*.h5'));
if isempty(entries)
    error('No completed .h5 scan files were found in %s.', inputFolder);
end
filesAnalyzed = sort(fullfile({entries.folder}, {entries.name}));

reference = [];
totalFrames = 0;
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

for fileIndex = 1:numel(filesAnalyzed)
    file = filesAnalyzed{fileIndex};
    info = h5info(file, '/images');
    for frameIndex = 1:info.Dataspace.Size(3)
        raw = h5read(file, '/images', [1 1 frameIndex], [nx ny 1]);
        frame = max(reshape(double(raw), nx, ny) - backgroundADU, 0);
        sumPixel = sumPixel + frame;
        sumPixelSquared = sumPixelSquared + frame .* frame;

        xProfile = sum(frame(:, yRange(1):yRange(2)), 2);
        xIntensity = accumarray(binIndex, xProfile, [nBins 1]) ./ binCounts;
        sumX = sumX + xIntensity;
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
denominator = meanXIntensity * meanXIntensity';
g2Matrix = nan(nBins, nBins);
valid = denominator > 0;
g2Matrix(valid) = meanXProducts(valid) ./ denominator(valid);
g2X = diag(g2Matrix);

figure('Name', 'Camera scan g2(0) and x-x correlations');
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
imagesc(xPixels, xPixels, g2Matrix);
axis image;
axis xy;
colorbar;
xlabel('x pixel (local ROI index)');
ylabel('x pixel (local ROI index)');
title('x-x normalized cross correlation');

fprintf('Analyzed %d files and %d frames from scan step %d.\n', ...
    numel(filesAnalyzed), totalFrames, reference{1});
fprintf('Results: g2Pixel, g2X, g2Matrix, xPixels, meanPixelIntensity, meanXIntensity.\n');
