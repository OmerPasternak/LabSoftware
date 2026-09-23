% Analyze axis-resolved intensity correlations in one completed camera scan step.
% Set inputFile, axisName, orthogonalRange, and binWidth below, then Run.
% Each /images frame contains raw ADU. For each frame, the chosen orthogonal
% band is averaged, then adjacent axis pixels are averaged into bins.
% g2Matrix(i,j) = <I_i I_j> / (<I_i><I_j>), averaged across frames.
% g2Axis = diag(g2Matrix) = <I_i^2> / <I_i>^2, consistent with the
% project's Python intensity g2(0) convention. This is zero-frame-lag
% spatial correlation, not temporal g2(tau) or a photon-count coincidence.

if ~exist('inputFile', 'var') || isempty(inputFile)
    [name, folder] = uigetfile('*.h5', 'Select a completed camera scan step');
    if isequal(name, 0)
        return;
    end
    inputFile = fullfile(folder, name);
end
if ~exist('axisName', 'var') || isempty(axisName)
    axisName = 'x';  % 'x' or 'y'
end
if ~exist('orthogonalRange', 'var') || isempty(orthogonalRange)
    orthogonalRange = [];  % [] uses the full orthogonal dimension; or [first last]
end
if ~exist('binWidth', 'var') || isempty(binWidth)
    binWidth = 8;  % adjacent axis pixels per bin; 1 gives pixel resolution
end
if ~exist('backgroundADU', 'var') || isempty(backgroundADU)
    backgroundADU = 0;  % scalar dark offset in ADU, clipped at zero
end

inputFile = char(inputFile);
axisName = lower(char(axisName));
if ~isfile(inputFile) || endsWith(lower(inputFile), '.partial')
    error('Select an existing completed .h5 file, not a .partial file.');
end
if ~ismember(axisName, {'x', 'y'})
    error('axisName must be ''x'' or ''y''.');
end
if ~isscalar(binWidth) || ~isfinite(binWidth) || binWidth < 1 || binWidth ~= fix(binWidth)
    error('binWidth must be a positive integer in pixels.');
end
if ~isscalar(backgroundADU) || ~isfinite(backgroundADU) || backgroundADU < 0
    error('backgroundADU must be a nonnegative scalar in ADU.');
end

info = h5info(inputFile, '/images');
if numel(info.Dataspace.Size) ~= 3
    error('/images must have three dimensions [x, y, frame] in MATLAB.');
end
% MATLAB reverses the Python/HDF5 dimension order for this dataset.
nx = info.Dataspace.Size(1);
ny = info.Dataspace.Size(2);
nFrames = info.Dataspace.Size(3);
if nFrames < 2
    error('At least two frames are required; found %d.', nFrames);
end
complete = h5readatt(inputFile, '/', 'complete');
if iscell(complete)
    complete = complete{1};  % h5py boolean attributes can arrive as {'TRUE'}
end
if ischar(complete) || isstring(complete)
    complete = strcmpi(complete, 'TRUE');
end
if ~isscalar(complete) || ~logical(complete)
    error('The scan-step file is marked incomplete.');
end
if double(h5readatt(inputFile, '/', 'frames_written')) ~= nFrames
    error('frames_written does not match the /images frame count.');
end

if axisName == 'x'
    nAxis = nx;
    nOrthogonal = ny;
else
    nAxis = ny;
    nOrthogonal = nx;
end
if isempty(orthogonalRange)
    orthogonalRange = [1 nOrthogonal];
end
if numel(orthogonalRange) ~= 2 || any(~isfinite(orthogonalRange)) || ...
        any(orthogonalRange ~= fix(orthogonalRange)) || ...
        orthogonalRange(1) < 1 || orthogonalRange(2) > nOrthogonal || ...
        orthogonalRange(1) > orthogonalRange(2)
    error('orthogonalRange must be [first last] within 1:%d.', nOrthogonal);
end

nBins = ceil(nAxis / binWidth);
binIndex = ceil((1:nAxis)' / binWidth);
binCounts = accumarray(binIndex, 1, [nBins 1]);
axisPixels = accumarray(binIndex, (1:nAxis)', [nBins 1]) ./ binCounts;
sumIntensity = zeros(nBins, 1);
sumProducts = zeros(nBins, nBins);

for frameIndex = 1:nFrames
    raw = h5read(inputFile, '/images', [1 1 frameIndex], [nx ny 1]);
    frame = reshape(double(raw), nx, ny);
    frame = max(frame - backgroundADU, 0);
    if axisName == 'x'
        profile = mean(frame(:, orthogonalRange(1):orthogonalRange(2)), 2);
    else
        profile = mean(frame(orthogonalRange(1):orthogonalRange(2), :), 1)';
    end
    intensity = accumarray(binIndex, profile, [nBins 1]) ./ binCounts;
    sumIntensity = sumIntensity + intensity;
    sumProducts = sumProducts + intensity * intensity';
end

meanIntensity = sumIntensity / nFrames;
meanProducts = sumProducts / nFrames;
denominator = meanIntensity * meanIntensity';
g2Matrix = nan(nBins, nBins);
valid = denominator > 0;
g2Matrix(valid) = meanProducts(valid) ./ denominator(valid);
g2Axis = diag(g2Matrix);

figure('Name', 'Axis-resolved g2(0)');
tiledlayout(1, 2);
nexttile;
plot(axisPixels, g2Axis, '.-');
xlabel(sprintf('%s pixel (local ROI index)', axisName));
ylabel('g^{(2)}(0)');
grid on;
nexttile;
imagesc(axisPixels, axisPixels, g2Matrix);
axis image;
axis xy;
colorbar;
xlabel(sprintf('%s pixel (local ROI index)', axisName));
ylabel(sprintf('%s pixel (local ROI index)', axisName));
title('Normalized zero-lag cross correlation');

fprintf('Analyzed %d frames, %d axis bins (%d pixels/bin).\n', ...
    nFrames, nBins, binWidth);
fprintf('Results in workspace: axisPixels, meanIntensity, g2Axis, g2Matrix.\n');
