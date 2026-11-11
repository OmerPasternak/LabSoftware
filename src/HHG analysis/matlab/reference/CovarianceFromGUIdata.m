ShowScanGUI

%% From loaded data - 'Load scan'  taken with 'piezo NoMove' - statistics scan.
dataNow = permute( data, [2 1 3]);

%% Calibrate the x harmonic axis.
xW = wAxis_Calibration(data, true,[19,21,23,25]); 

%% Change here parameters:
spacePix  = 110:140;
% spacePix  = 60:80;
lims = [11,29];     % Where are the harmonics of interest

%% Plot DC - use this to choose the spatial pixels to average over
figure; hold on;
% Display the mean of dataNow across the third dimension
imagesc(squeeze(mean(dataNow, 3)));
colorbar; set(gca, 'ColorScale', 'log'); caxis([90 200]);


%% normalized cov
covNow = corrcoef(squeeze((mean(dataNow(spacePix,:,:),1)))');

%coherent only reduced:
%covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))'-squeeze((mean(dataNow1(spacePix,:,:),1)))');
%covNow =  cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');

figure;hold on
if exist('xW','var')
    pcolor(xW,xW,covNow)
    shading interp
else
    imagesc(covNow)
end

colorbar
%caxis([-1 1]*10^(-1))
caxis([-1 1])

set(gca,'FontSize',20)



%coherent only reduced:
%covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))')-cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');
%covNow =  cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');


%plot(fliplr(mean(dataNow(spacePix,:,:),[1,3])+1250),'r','LineWidth',4)
%caxis([-1 1]*10^(3))
 % plot(xW,(normalizeData(mean(dataNow(spacePix,:,:),[1,3])))*5+15,'r','LineWidth',4)
% plot(xW,(normalizeData(var(mean(dataNow(spacePix,:,:),[1])),[],3))*5+15,'r','LineWidth',4)

if exist('xW','var')
    axis equal;
    xlim(lims)
    ylim(lims)
    for hPlus = -4:2:4
    %hPlus = -2;
    %{
    % Remove the lines by commenting them out
        xline(16+hPlus,'g--','linewidth',2  )
%         xline(15.2+hPlus,'g--','linewidth',2)
        xline(15.4+hPlus,'b--','linewidth',2)
        xline(15.6+hPlus,'r--','linewidth',2)
        xline(15.8+hPlus,'r-','linewidth',2)
        xline(15.8+hPlus,'b-','linewidth',2)
        xline(16.2+hPlus,'b-','linewidth',2)
        xline(16.2+hPlus,'r-','linewidth',2)
        xline(16.4+hPlus,'r--','linewidth',2)
        xline(16.6+hPlus,'b--','linewidth',2)
        xline(22.4+hPlus,'r--','linewidth',2)
        xline(22.6+hPlus,'b--','linewidth',2)
%         xline(16.8+hPlus,'g--','linewidth',2)


        yline(16+hPlus,'g--','linewidth',2  )
%         yline(15.2+hPlus,'g--','linewidth',2)
        yline(15.4+hPlus,'b--','linewidth',2)
        yline(15.6+hPlus,'r--','linewidth',2)
        yline(15.8+hPlus,'r-','linewidth',2)
        yline(15.8+hPlus,'b-','linewidth',2)
        yline(16.2+hPlus,'b-','linewidth',2)
        yline(16.2+hPlus,'r-','linewidth',2)
        yline(16.4+hPlus,'r--','linewidth',2)
        yline(16.6+hPlus,'b--','linewidth',2)
%         yline(16.8+hPlus,'g--','linewidth',2)
    %}

    end
end



%% unsorted statistical repeats
toPlotStatReps = squeeze(mean(dataNow(spacePix,:,:),1));
figure;hold on
if exist('xW','var')
    pcolor(1:size(toPlotStatReps,2),xW,toPlotStatReps)
    shading flat
    ylim([xW(end) 22])
    xlim([1 size(toPlotStatReps,2)])
else
    imagesc(toPlotStatReps)
end

colorbar
caxis([90 130])

%Extra commands for better visibility
xlim([1200 1600])


%% non-normalized cov



%recently loaded data - non normalized
covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))');

%coherent only reduced:
%covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))'-squeeze((mean(dataNow1(spacePix,:,:),1)))');
%covNow =  cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');

figure;hold on
if exist('xW','var')
    pcolor(xW,xW,covNow)
    shading flat
else
    imagesc(covNow)
end

colorbar
%caxis([-1 1]*10^(3))
set(gca,'FontSize',20)



%coherent only reduced:
%covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))')-cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');
%covNow =  cov(squeeze((mean(dataNow1(spacePix,:,:),1)))');


%plot(fliplr(mean(dataNow(spacePix,:,:),[1,3])+1250),'r','LineWidth',4)
%caxis([-1 1]*10^(3))

if exist('xW','var')
    axis equal;
    xlim([xW(end) 26])
    ylim([xW(end) 26])
    plot(xW,(normalizeData(mean(dataNow(spacePix,:,:),[1,3])))*5+15,'r','LineWidth',4)
    for hPlus = -4:2:4
    %hPlus = -2;
        xline(16+hPlus,'g--','linewidth',2  )
        xline(15.2+hPlus,'g--','linewidth',2)
        xline(15.4+hPlus,'b--','linewidth',2)
        xline(15.6+hPlus,'r--','linewidth',2)
        xline(15.8+hPlus,'r-','linewidth',2)
        xline(15.8+hPlus,'b-','linewidth',2)
        xline(16.2+hPlus,'b-','linewidth',2)
        xline(16.2+hPlus,'r-','linewidth',2)
        xline(16.4+hPlus,'r--','linewidth',2)
        xline(16.6+hPlus,'b--','linewidth',2)
        xline(16.8+hPlus,'g--','linewidth',2)


        yline(16+hPlus,'g--','linewidth',2  )
        yline(15.2+hPlus,'g--','linewidth',2)
        yline(15.4+hPlus,'b--','linewidth',2)
        yline(15.6+hPlus,'r--','linewidth',2)
        yline(15.8+hPlus,'r-','linewidth',2)
        yline(15.8+hPlus,'b-','linewidth',2)
        yline(16.2+hPlus,'b-','linewidth',2)
        yline(16.2+hPlus,'r-','linewidth',2)
        yline(16.4+hPlus,'r--','linewidth',2)
        yline(16.6+hPlus,'b--','linewidth',2)
        yline(16.8+hPlus,'g--','linewidth',2)


    end
end

%% normalized cov
covNow = corrcoef(squeeze((mean(dataNow(spacePix,:,:),1)))');
figure; hold on
imagesc(flipud(fliplr(covNow)))
colorbar
plot(fliplr(mean(dataNow(spacePix,:,:),[1,3])+1250),'r','LineWidth',4)
%caxis([-1 1]*10^(3))


%% normalized cov calibrated energies
covNow = corrcoef(squeeze((mean(dataNow(spacePix,:,:),1)))');
figure; hold on
pcolor(xW,xW,covNow)
shading interp
colorbar
%plot(fliplr(mean(dataNow(spacePix,:,:),[1,3])+1250),'r','LineWidth',4)
%caxis([-1 1]*10^(3))

plot(xW,(normalizeData(mean(dataNow(spacePix,:,:),[1,3])))*5+15,'r','LineWidth',4)

xlim([xW(end) 22])
ylim([xW(end) 22])

for hPlus = -4:2:4
%hPlus = -2;
%{
    xline(16+hPlus,'g--','linewidth',2  )
%     xline(15.2+hPlus,'g--','linewidth',2)
    xline(15.4+hPlus,'b--','linewidth',2)
    xline(15.6+hPlus,'r--','linewidth',2)
    xline(15.8+hPlus,'r-','linewidth',2)
    xline(15.8+hPlus,'b-','linewidth',2)
    xline(16.2+hPlus,'b-','linewidth',2)
    xline(16.2+hPlus,'r-','linewidth',2)
    xline(16.4+hPlus,'r--','linewidth',2)
    xline(16.6+hPlus,'b--','linewidth',2)
%     xline(16.8+hPlus,'g--','linewidth',2)
    
    
     yline(16+hPlus,'g--','linewidth',2  )
%     yline(15.2+hPlus,'g--','linewidth',2)
    yline(15.4+hPlus,'b--','linewidth',2)
    yline(15.6+hPlus,'r--','linewidth',2)
    yline(15.8+hPlus,'r-','linewidth',2)
    yline(15.8+hPlus,'b-','linewidth',2)
    yline(16.2+hPlus,'b-','linewidth',2)
    yline(16.2+hPlus,'r-','linewidth',2)
    yline(16.4+hPlus,'r--','linewidth',2)
    yline(16.6+hPlus,'b--','linewidth',2)
%     yline(16.8+hPlus,'g--','linewidth',2)
%}
end


%% diagonal of covariance matrix
covNow = cov(squeeze((mean(dataNow(spacePix,:,:),1)))');

figure; plot(diag(covNow))

%% cov line

figure;
covNow = corrcoef(squeeze((mean(dataNow(spacePix,:,:),1)))');
plot(covNow(400,:))


%% var

figure;
pcolor(squeeze(var(single((dataNow(:,:,:))),[],3)))
shading flat
colorbar
caxis([0 20])
%% var line

figure;
plot(squeeze(var(single(mean(dataNow(spacePix,:,:),1)),[],3)))



%% space cov


freqChoose = 1295:1305;
freqChoose = 1150:1190;
%freqChoose = 456;

%normalized
covNow = corrcoef(squeeze((mean(dataNow(:,freqChoose,:),1)))');
% non normalized
%covNow = cov(squeeze((mean(data(freqChoose,:,:),1)))');
figure;imagesc(fliplr(covNow))
colorbar
%caxis([-1 1]*10^(3))

%% PCA for space
[pcaComp,pcaScore,pcaLatent] = pca(squeeze((mean(dataNow(:,freqChoose,:),2)))');


figure;hold on
plot(pcaComp(:,1:5).*pcaLatent(1:5)')

figure;hold on
plot(pcaComp(:,1:5))


figure;hold on
plot(pcaLatent(1:5)/sum(pcaLatent))





%% PCA for freq
[pcaComp,pcaScore,pcaLatent] = pca(squeeze((mean(dataNow(spacePix,:,:),1)))');

%%
figure;hold on
plot(xW,pcaComp(:,1:5).*pcaLatent(1:5)')

%figure;hold on
%plot(pcaComp(:,1:5))



xline(16+hPlus,'g--','linewidth',2  )
xline(15.2+hPlus,'g--','linewidth',2)
xline(15.4+hPlus,'b--','linewidth',2)
xline(15.6+hPlus,'r--','linewidth',2)
xline(15.8+hPlus,'r-','linewidth',2)
xline(15.8+hPlus,'b-','linewidth',2)
xline(16.2+hPlus,'b-','linewidth',2)
xline(16.2+hPlus,'r-','linewidth',2)
xline(16.4+hPlus,'r--','linewidth',2)
xline(16.6+hPlus,'b--','linewidth',2)
xline(16.8+hPlus,'g--','linewidth',2)

figure;hold on
plot(pcaLatent(1:5)/sum(pcaLatent))

%% single images

figure;
pcolor(flipud(fliplr(data(:,:,4)')))
shading interp
colorbar
caxis([90 200])
