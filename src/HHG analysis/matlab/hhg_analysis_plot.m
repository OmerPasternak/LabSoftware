function hhg_analysis_plot(source,r) %#ok<INUSD>
%HHG_ANALYSIS_PLOT Preserve the source scripts' pcolor/imagesc conventions.
% pcolor flat intentionally omits boundary cells, as MATLAB's original does.
f=figure('Name',['HHG analysis: ' r.action]);
% Source script figures use MATLAB's session-default palette. The GUIDE
% viewer retains its separate saved .fig palette; do not conflate them.
z=r.values; xW=r.xW; opts=r.settings;
switch r.action
    case 'dc'
        hold on; imagesc(z); colorbar; set(gca,'ColorScale','log'); caxis([90 200]);
    case {'pearson','covariance','calibrated_pearson'}
        hold on;
        if isempty(xW)
            imagesc(z);
        else
            pcolor(xW,xW,z);
            if contains(r.action,'pearson'), shading interp; else, shading flat; end
            axis equal; xlim(opts.harmonic_limits); ylim(opts.harmonic_limits);
        end
        colorbar; set(gca,'FontSize',20);
        if contains(r.action,'pearson'), caxis([-1 1]); end
        if ~isempty(xW)&&ismember(r.action,{'covariance','calibrated_pearson'})
            hold on; plot(xW,normalizeData(r.mean_spectrum)*5+15,'r','LineWidth',4);
            guides(strcmp(r.action,'covariance'));
        end
    case 'repeats'
        hold on;
        if isempty(xW), imagesc(z);
        else, pcolor(1:size(z,2),xW,z); shading flat; end
        colorbar; caxis([90 130]); xlabel('Repeat index');
        if size(z,2)>=1600, xlim([1200 1600]); end
    case 'flipped_pearson'
        hold on; imagesc(flipud(fliplr(z))); colorbar;
        plot(fliplr(r.mean_spectrum)+1250,'r','LineWidth',4);
    case 'covariance_diagonal'
        plot(z); xlabel('Spectral pixel'); ylabel('Sample variance of spatial mean (ADU^2)');
    case 'pearson_slice'
        plot(z); xlabel('Spectral pixel'); ylabel(sprintf('Pearson row %d',opts.slice));
    case 'variance_image'
        pcolor(z); shading flat; colorbar; caxis([0 20]);
    case 'variance_line'
        plot(z); xlabel('Spectral pixel'); ylabel('Sample variance of spatial mean (ADU^2)');
    case 'legacy_space'
        imagesc(z); colorbar;
    case {'pca_space','pca_frequency'}
        n=min(5,size(r.coefficients,2));
        weighted=r.coefficients(:,1:n).*r.latent(1:n)';
        if strcmp(r.action,'pca_frequency')&&~isempty(xW), plot(xW,weighted); else, plot(weighted); end
        if strcmp(r.action,'pca_space')
            figure('Name','Spatial PCA components'); plot(r.coefficients(:,1:n));
        end
        figure('Name','PCA explained fractions'); plot(r.explained_fraction(1:n));
    case 'single_image'
        pcolor(z); shading interp; colorbar; caxis([90 200]);
    case 'tc'
        hold on;
        if opts.tc_fs, x=r.delay_fs; label='Delay (fs; historical 1600 nm calibration)';
        else, x=r.delay_mm; label='TC position (mm)'; end
        if isempty(xW), imagesc(x,1:size(z,1),z);
        else, pcolor(x,xW,z); shading flat; ylim([12 26]); end
        xlabel(label); colorbar; caxis([0 2500]); set(gca,'FontSize',25);
    case 'tc_fft'
        imagesc([r.frequencies(1) r.frequencies(end)],1:size(z,1),z);
        colorbar; hhg_analysis_devon(f); caxis([0 1]); set(gca,'FontSize',50);
        xlabel('Fourier frequency / fundamental');
end
end

function guides(full)
for offset=-4:2:4
    values=[16 15.4 15.6 15.8 15.8 16.2 16.2 16.4 16.6];
    styles={'g--','b--','r--','r-','b-','b-','r-','r--','b--'};
    if full, values=[values 15.2 16.8]; styles=[styles {'g--','g--'}]; end %#ok<AGROW>
    for k=1:numel(values)
        xline(values(k)+offset,styles{k},'LineWidth',2);
        yline(values(k)+offset,styles{k},'LineWidth',2);
    end
end
end
