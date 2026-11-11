% Run sections after Export data to workspace. Missing calibration/FFT
% helpers remain listed in INACTIVE_FEATURES.txt; original equations follow.
%% A script to examine 1D TC experiments quickly in the lab.
%% Load data directly by 'Export data to workspace'
ShowScanGUI

%% Get BSVOFF data - single image
dataBSVOFF = permute(data, [2 1]);% permute( data, [2 1 3]);

%% Calibrate harmonic axis.
xW = wAxis_Calibration(dataBSVOFF, true, [13,15,17,19,25,27],6); % The order of harmonics, how many harmonics

%% Get TCS data
dataRaw = permute( data, [2 1 3]); % Only run this once, to use the current data loaded from GUI!!!!
dataNow = dataRaw;

%% Change here parameters
TCSAxis = (26.7:0.001:27.001);
ShowInFS = true;  % true - xaxis is time in fs.    false - xaxis is TCS.
spaceChoose = 1:500;  % This is the y axis to average over. Choose based on a plot DC

% Constants calculations - no need to edit
num_of_TCdelays = numel(TCSAxis)-1; 
assert(num_of_TCdelays == size(dataNow,3),'Make sure TC scan range is defined well')

%% Remove BG if you wish
dataNow = dataRaw - repmat(dataBSVOFF, [1,1,num_of_TCdelays]);

%% Plot DC - use this to choose the spatial pixels to average over
figure 
hold on

%DC
imagesc(squeeze(mean(dataNow,3)))

%plot first
% delayToPlot = 1;
% imagesc(squeeze(mean(dataNow(:,:,delayToPlot),3)))

colorbar
% set(gca, 'ColorScale', 'log')
caxis([0 1000])

%% Set Fourier parameters - Automatic. the calibration is currently according to 1600nm delays observed in the past
switch round(TCSAxis(2)-TCSAxis(1),3)
    case 0.0010
        stepToWave = 1/(5.8*4); % for 0.001 TC step in units of 1 omega fundamental
    case 0.0020
        stepToWave = 1/(5.8*2); % for 0.002 TC step in units of 1 omega fundamental
    case 0.0030
        stepToWave = 1/(5.8*4/3); % for 0.001 TC step in units of 1 omega fundamental
    case 0.0100
        stepToWave = 1/(5.8*4/10); % for 0.01 TC step in units of 1 omega fundamental
    case 0.0500
        stepToWave = 1/(5.8*4/50); % for 0.05 TC step in units of 1 omega fundamental
    case 0.0050
        stepToWave = 1/(5.8*4/5); % for 0.005 TC step in units of 1 omega fundamental
end
fIR = 1; 
arbDelay = (1: size(dataNow,3))*stepToWave;
dt = arbDelay(2) - arbDelay(1);
Fs = 1/dt; % Sampling rate in Hz
samplesPer1Omega = Fs/fIR; % how many samples in 1 omega period.
startNow =1;
stopNow = length(arbDelay);

L2 = stopNow-startNow;
freqs = (Fs*(0:floor(L2/2))/L2)/fIR; 

freqPointsPer1Omega = 1/( Fs/L2/fIR);
arbDelay = arbDelay * 2.67 ; % delay in fs

%% 2 plots - delay and FFT
% Total signal in delay
figure('WindowState','maximized')
% no DC removal
timeS = squeeze(squeeze(mean(dataNow(spaceChoose,:,:),1)));
% Remove DC
%timeS = timeS - mean(timeS,2);

% currently loaded
if exist('xW','var')
    if ShowInFS
        pcolor(arbDelay,xW,timeS)                          % temporal fs XAXIS
    else
        pcolor(TCSAxis(1,1:num_of_TCdelays),xW,timeS)      % TC delay XAXIS
    end
    shading flat
else
    if ShowInFS
        imagesc(timeS,'x',arbDelay)
    else
        imagesc(timeS,'x',TCSAxis)
    end
end
%Difference 
%imagesc(timeS-timeS1,'x',arbDelay)


ax = gca;
ax.FontSize= 25;
colorbar
%crameri('devon')
caxis([0 2500])
%xlim([14 16])
% ylim([xW(end) 27])  % xW comes from 'w_axis_auto_calib' script.
ylim([12 26])

delta = 0;%Correct calibration
lineW = 1.5;

%{  
% To uncomment - Add another '%' in front of the line above with the '%%{'.
yline(19+0.6*2-delta,'c-.','linewidth',lineW)
yline(17+0.6*2-delta,'c-.','linewidth',lineW)
% yline(18.6-delta,'c--','linewidth',lineW)
% yline(18.4-delta,'r--','linewidth',lineW)
% yline(20.6-delta,'c--','linewidth',lineW)
% yline(20.4-delta,'r--','linewidth',lineW)

yline(24-delta,'g--','linewidth',lineW)
yline(22-delta,'g--','linewidth',lineW)
yline(20-delta,'g--','linewidth',lineW)
yline(18-delta,'g--','linewidth',lineW)
yline(16-delta,'g--','linewidth',lineW)
yline(14-delta,'g--','linewidth',lineW)
yline(12-delta,'g--','linewidth',lineW)

yline(11-delta,'k--','linewidth',lineW);
yline(13-delta,'k--','linewidth',lineW);
yline(15-delta,'k--','linewidth',lineW);
yline(17-delta,'k--','linewidth',lineW);
yline(19-delta,'k--','linewidth',lineW);
yline(21-delta,'k--','linewidth',lineW);
yline(23-delta,'k--','linewidth',lineW);
yline(25-delta,'k--','linewidth',lineW);

%yline(16+0.6-delta,'c--','linewidth',lineW)
%yline(16+0.4-delta,'r--','linewidth',lineW)

yline(24-0.4-delta,'r--','linewidth',lineW)
yline(24-0.6-delta,'c--','linewidth',lineW)
yline(22-0.4-delta,'r--','linewidth',lineW)
yline(22-0.6-delta,'c--','linewidth',lineW)
yline(20-0.4-delta,'r--','linewidth',lineW)
yline(20-0.6-delta,'c--','linewidth',lineW)
yline(18-0.6-delta,'c--','linewidth',lineW)
yline(18-0.4-delta,'r--','linewidth',lineW)
yline(16-0.6-delta,'c--','linewidth',lineW)
yline(16-0.4-delta,'r--','linewidth',lineW)
yline(14-0.4-delta,'r--','linewidth',lineW)
yline(14-0.6-delta,'c--','linewidth',lineW)
yline(12-0.4-delta,'r--','linewidth',lineW)
yline(12-0.6-delta,'c--','linewidth',lineW)

yline(25-0.6-delta,'r--','linewidth',lineW)
yline(25-0.4-delta,'c--','linewidth',lineW)
yline(23-0.6-delta,'r--','linewidth',lineW)
yline(23-0.4-delta,'c--','linewidth',lineW)
yline(21-0.6-delta,'r--','linewidth',lineW)
yline(21-0.4-delta,'c--','linewidth',lineW)
yline(19-0.6-delta,'r--','linewidth',lineW)
yline(19-0.4-delta,'c--','linewidth',lineW)
yline(17-0.6-delta,'r--','linewidth',lineW)
yline(17-0.4-delta,'c--','linewidth',lineW)
yline(15-0.6-delta,'r--','linewidth',lineW)
yline(15-0.4-delta,'c--','linewidth',lineW)
yline(13-0.6-delta,'r--','linewidth',lineW)
yline(13-0.4-delta,'c--','linewidth',lineW)
yline(11-0.6-delta,'r--','linewidth',lineW)
yline(11-0.4-delta,'c--','linewidth',lineW)

caxis([0,700]);


%}
%% FFT results
% Total delay FT spectrum
figure('WindowState','maximized')
% no DC removal
absFT = abs(fftPerCell( squeeze(dataNow(spaceChoose,:,:))));
imagesc(squeeze(mean(absFT,1)),'x',[freqs(1),freqs(end)])
%DC norm
imagesc(squeeze(mean(absFT,1))./squeeze(mean(absFT(:,1),1)),'x',[freqs(1),freqs(end)])
ax = gca;
ax.FontSize= 50;
colorbar
crameri('devon')
%xlim([14 16])
caxis([0 1])
hold on
title([ 'Y ' num2str(spaceChoose(1)) ' to ' num2str(spaceChoose(end)) ] )  

