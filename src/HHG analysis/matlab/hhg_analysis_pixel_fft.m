function result = hhg_analysis_pixel_fft(data,delay,label,makePlots)
%HHG_ANALYSIS_PIXEL_FFT Recreated per-pixel temporal Fourier magnitude viewer.
% data: [x,y,delay], two or more uniformly sampled delays, no preprocessing.
% Explicit convention: abs(fft(data,[],3)), positive bins 0:floor(N/2), no
% window, DC removal, padding, amplitude doubling, normalization or averaging.
% Historical helper conventions are unavailable. ROI/pixel aggregation is
% intentionally inactive at the user's request; only a selected pixel is shown.
if nargin<4, makePlots=true; end
n=size(data,3);
hhg_analysis_validate_fft_axis(delay,n); steps=diff(double(delay));
assert(numel(data)*8*4<=1024^3,'HHG:Memory','Per-pixel Fourier workspace exceeds 1 GiB. Select a smaller ROI or explicit bin size.');
spectrum=fft(double(data),[],3);
amplitude=abs(spectrum(:,:,1:floor(n/2)+1));
result=struct('magnitude',amplitude,'cycles_per_delay_unit',(0:floor(n/2))/(n*abs(steps(1))), ...
    'delay',delay,'label',label,'method','abs(fft(double(data),[],3)); positive half; no normalization', ...
    'aggregation','INACTIVE: historical ROI/pixel aggregation is unknown');
if ~makePlots, return; end
f=figure('Name',['Per-pixel Fourier — ' label],'Position',[100 100 1100 700]);
layout=tiledlayout(f,2,2,'Padding','compact');
axMap=nexttile(layout); axTrace=nexttile(layout); axSpectrum=nexttile(layout,[1 2]);
panel=uipanel(f,'Units','normalized','Position',[0 0 1 0.08]);
uicontrol(panel,'Style','text','Units','normalized','Position',[0 .1 .6 .7], ...
    'String','Selected pixel only. Aggregation inactive; recreated FFT convention shown in exported result.');
xControl=uicontrol(panel,'Style','edit','Units','normalized','Position',[.62 .1 .07 .7],'String','1');
yControl=uicontrol(panel,'Style','edit','Units','normalized','Position',[.70 .1 .07 .7],'String','1');
frequencyControl=uicontrol(panel,'Style','edit','Units','normalized','Position',[.78 .1 .07 .7], ...
    'String',num2str(min(2,size(amplitude,3))));
uicontrol(panel,'Style','pushbutton','Units','normalized','Position',[.86 .1 .13 .7], ...
    'String','Update x,y,bin','Callback',@(~,~)draw());
layout.OuterPosition=[0 .09 1 .91];
draw();
    function draw()
        x=str2double(get(xControl,'String')); y=str2double(get(yControl,'String'));
        k=str2double(get(frequencyControl,'String'));
        if ~all(isfinite([x y k]))||any([x y k]~=fix([x y k]))|| ...
                x<1||x>size(data,1)||y<1||y>size(data,2)||k<1||k>size(amplitude,3)
            errordlg('Enter valid one-based x, y and Fourier-bin indices.','Fourier selection'); return;
        end
        imagesc(axMap,amplitude(:,:,k)'); axis(axMap,'xy'); colorbar(axMap);
        xlabel(axMap,'x pixel / bin'); ylabel(axMap,'y pixel / bin');
        title(axMap,sprintf('|FFT| at %.6g cycles / delay unit',result.cycles_per_delay_unit(k)));
        plot(axTrace,delay,reshape(data(x,y,:),[],1)); xlabel(axTrace,'Loaded delay / frame index');
        ylabel(axTrace,'Pixel / binned pixel (ADU)'); title(axTrace,sprintf('x=%d, y=%d',x,y));
        plot(axSpectrum,result.cycles_per_delay_unit,reshape(amplitude(x,y,:),[],1));
        xlabel(axSpectrum,'Cycles / loaded delay unit (no optical-frequency inference)');
        ylabel(axSpectrum,'Unnormalized |FFT|');
    end
end
