% Analyze all completed camera scan files from one scan step in a folder.
% Set inputFolder below, or leave it empty to use the repository's data folder.
% /images is [frame,y,x] in Python and [x,y,frame] in MATLAB. Values are ADU.
%
% g2Pixel(x,y) = <I(x,y)^2> / <I(x,y)>^2 across all frames.
% For each frame, J(x) = sum_y I(x,y) over yRange. pearsonMatrix is the
% Pearson correlation between J(x1) and J(x2) across frames. g2X is the
% intensity moment <J(x)^2>/<J(x)>^2, separate from Pearson's diagonal.
% These are zero-frame-lag camera ADU statistics, not g2(tau) or photon
% coincidences. Zero-mean g2 and zero-variance Pearson values return NaN.

% INPUT FOLDER: paste the full path to the folder containing completed .h5 files.
% Example: inputFolder = 'D:\CameraTests';
% Leave empty to use this project's data folder. Files in subfolders are not read.
inputFolder = '';

defaultFolder = fullfile(fileparts(mfilename('fullpath')), '..', 'data');
if ~exist('inputFolder', 'var') || isempty(inputFolder) || isequal(inputFolder, 0)
    inputFolder = defaultFolder;
elseif ~(ischar(inputFolder) || (isstring(inputFolder) && isscalar(inputFolder)))
    warning('Invalid inputFolder value. Using the project data folder: %s', defaultFolder);
    inputFolder = defaultFolder;
elseif ~isfolder(inputFolder)
    warning('inputFolder does not exist: %s. Using the project data folder: %s', ...
        char(inputFolder), defaultFolder);
    inputFolder = defaultFolder;
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

canonicalFolder=fullfile(fileparts(mfilename('fullpath')),'..','src','HHG analysis','matlab');
addpath(canonicalFolder);
axisResult=hhg_analysis_axis_g2(inputFolder,yRange,binWidth,backgroundADU,batchFrames,true);
g2Pixel=axisResult.g2Pixel; g2X=axisResult.g2X; pearsonMatrix=axisResult.pearsonMatrix;
xPixels=axisResult.xPixels; meanPixelIntensity=axisResult.meanPixelIntensity;
meanXIntensity=axisResult.meanXIntensity; totalFrames=axisResult.totalFrames;
filesAnalyzed=axisResult.filesAnalyzed; framesPerBatch=axisResult.framesPerBatch; nBins=axisResult.nBins;
