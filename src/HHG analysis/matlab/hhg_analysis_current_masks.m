function result = hhg_analysis_current_masks(fig,action,path,makePlots)
%HHG_ANALYSIS_CURRENT_MASKS Apply source .msk windows to current HDF5 batches.
% Mask rows: [harmonic, x, bottom-origin y, width, height], one-based.
% All harmonic traces are reduced in one pass through the camera source.
if nargin<4, makePlots=true; end
h=guidata(fig);
if nargin<3
    if isappdata(fig,'hhg_mask_path'), path=getappdata(fig,'hhg_mask_path'); else, path=''; end
    if isempty(path)||strcmp(action,'pushbutton26')
        [f,p]=uigetfile('*.msk','Choose a harmonic mask');
        if isequal(f,0), result=[]; return; end
        path=fullfile(p,f);
    end
end
mask=readmatrix(path,'FileType','text');
assert(size(mask,2)==5&&~isempty(mask)&&all(isfinite(mask),'all'),'HHG:Mask','Mask must contain five finite columns: H,x,y,width,height.');
assert(numel(unique(mask(:,1)))==size(mask,1),'HHG:Mask','Harmonic labels must be unique.');
rects=mask(:,2:5);
assert(all(rects==fix(rects)&rects>=1,'all')&&all(rects(:,1)+rects(:,3)-1<=h.width)&& ...
    all(rects(:,2)+rects(:,4)-1<=h.height),'HHG:Mask','A harmonic mask window is outside the source geometry.');
setappdata(fig,'hhg_mask_path',path);
[~,name,extension]=fileparts(path); set(h.edit3,'String',[name extension],'TooltipString',path);
if strcmp(action,'pushbutton26'), result=[]; return; end
exportPixels=ismember(action,{'pushbutton25','pushbutton27'});
result=struct('orders',mask(:,1),'mask',mask,'mask_path',path,'traces',zeros(h.flength,size(mask,1)), ...
    'delay',h.delay,'axis_label',getappdata(fig,'hhg_delay_label'),'source',getappdata(fig,'hhg_hdf5_source'));
pixels=cell(size(mask,1),1);
if exportPixels
    assert(8*sum(rects(:,3).*rects(:,4))*h.flength<=256*1024^2,'HHG:Memory','Harmonic pixel export exceeds 256 MiB. Select smaller explicit masks.');
    for j=1:size(mask,1), pixels{j}=zeros(rects(j,4),rects(j,3),h.flength); end
end
batch=hhg_analysis_batch_size(h.width,h.height);
for first=1:batch:h.flength
    count=min(batch,h.flength-first+1); data=flip(hhg_analysis_read(fig,first,count),2);
    for j=1:size(mask,1)
        bounds=rects(j,:);
        roi=data(bounds(1):bounds(1)+bounds(3)-1,bounds(2):bounds(2)+bounds(4)-1,:);
        result.traces(first:first+count-1,j)=reshape(sum(sum(roi,1),2),[],1);
        if exportPixels, pixels{j}(:,:,first:first+count-1)=permute(roi,[2 1 3]); end
    end
    drawnow limitrate nocallbacks;
end
result.mean_yield=mean(result.traces,1);
if strcmp(action,'pushbutton21')
    % Keep the source's factor-2 optical-path option explicit for metadata
    % axes; frame indices must not be exported as physical optical delays.
    answer=inputdlg({'Coordinate multiplier (source piezo CSV uses 2; metadata units shown in result)'}, ...
        'Mask trace coordinate',1,{'1'});
    if isempty(answer), return; end
    factor=str2double(answer{1}); assert(isscalar(factor)&&isfinite(factor),'HHG:Mask','Enter a finite coordinate multiplier.');
    folder=uigetdir(fileparts(path),'Choose derived CSV output directory');
    if isequal(folder,0), return; end
    result.coordinate_multiplier=factor;
    for j=1:size(mask,1)
        hhg_analysis_write_csv(fullfile(folder,sprintf('H%s_trace.csv',num2str(mask(j,1)))),[h.delay(:)*factor result.traces(:,j)]);
    end
    hhg_analysis_write_csv(fullfile(folder,'harmonic_mean_yield.csv'),[mask(:,1) result.mean_yield(:)]);
end
if exportPixels
    prefix=get(h.edit2,'String'); assert(isvarname(prefix),'HHG:Prefix','Use a valid MATLAB variable prefix.');
    for j=1:size(mask,1)
        name=matlab.lang.makeValidName([prefix '_H' num2str(mask(j,1)) 'data']);
        assignin('base',name,pixels{j});
        if strcmp(action,'pushbutton27')
            hhg_analysis_pixel_fft(permute(pixels{j},[2 1 3]),h.delay,name,makePlots);
        end
    end
    assignin('base',[prefix '_piezoaxis'],h.delay); assignin('base',[prefix '_Hnumbase'],mask(:,1));
    assignin('base',[prefix '_filename'],h.filename); assignin('base','loadname',prefix);
end
setappdata(fig,'hhg_last_result',result);
end
