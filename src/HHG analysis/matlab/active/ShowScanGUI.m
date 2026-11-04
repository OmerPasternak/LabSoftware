function varargout = ShowScanGUI(varargin)
% SHOWSCANGUI MATLAB code for ShowScanGUI.fig
%      SHOWSCANGUI, by itself, creates a new SHOWSCANGUI or raises the existing
%      singleton*.
%
%      H = SHOWSCANGUI returns the handle to a new SHOWSCANGUI or the handle to
%      the existing singleton*.
%
%      SHOWSCANGUI('CALLBACK',hObject,eventData,handles,...) calls the local
%      function named CALLBACK in SHOWSCANGUI.M with the given input arguments.
%
%      SHOWSCANGUI('Property','Value',...) creates a new SHOWSCANGUI or raises the
%      existing singleton*.  Starting from the left, property value pairs are
%      applied to the GUI before ShowScanGUI_OpeningFcn gets called.  An
%      unrecognized property name or invalid value makes property application
%      stop.  All inputs are passed to ShowScanGUI_OpeningFcn via varargin.
%
%      *See GUI Options on GUIDE's Tools menu.  Choose "GUI allows only one
%      instance to run (singleton)".
%
% See also: GUIDE, GUIDATA, GUIHANDLES

% Edit the above text to modify the response to help ShowScanGUI

% Last Modified by GUIDE v2.5 02-Sep-2025 14:37:44

% Begin initialization code - DO NOT EDIT
gui_Singleton = 1;
gui_State = struct('gui_Name',       mfilename, ...
                   'gui_Singleton',  gui_Singleton, ...
                   'gui_OpeningFcn', @ShowScanGUI_OpeningFcn, ...
                   'gui_OutputFcn',  @ShowScanGUI_OutputFcn, ...
                   'gui_LayoutFcn',  [] , ...
                   'gui_Callback',   []);
if nargin && ischar(varargin{1})
    gui_State.gui_Callback = str2func(varargin{1});
end

if nargout
    [varargout{1:nargout}] = gui_mainfcn(gui_State, varargin{:});
else
    gui_mainfcn(gui_State, varargin{:});
end
% End initialization code - DO NOT EDIT


% --- Executes just before ShowScanGUI is made visible.
function ShowScanGUI_OpeningFcn(hObject, eventdata, handles, varargin)
% This function has no output args, see OutputFcn.
% hObject    handle to figure
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
% varargin   command line arguments to ShowScanGUI (see VARARGIN)

set(handles.text7, 'String', ['Current delay: ' num2str(0, '%10.2f') ' um']);
set(handles.text2, 'String', ['None loaded']);

set(handles.slider1, 'Value', 1);
set(handles.slider1, 'Min', 1);
set(handles.slider1, 'Max', 10);
set(handles.slider1, 'SliderStep', [1/10 1/10]);
set(handles.slider1, 'Enable', 'off');
set(handles.pushbutton2, 'Enable', 'off');
set(handles.pushbutton3, 'Enable', 'off');
set(handles.radiobutton1, 'Enable', 'off');
set(handles.radiobutton2, 'Enable', 'off');

handles.delay = 1;
handles.scale = 0;
handles.roix = 1;
handles.roiy = 1;
handles.roiwidth = 50;
handles.roiheight = 50;
handles.roi = 0;
handles.loaded = 0;  %No file loaded
handles.bg = 0;
handles.maskfile = 0;

% Choose default command line output for ShowScanGUI
handles.output = hObject;

% Update handles structure
guidata(hObject, handles);


% UIWAIT makes ShowScanGUI wait for user response (see UIRESUME)
% uiwait(handles.figure1);


% --- Outputs from this function are returned to the command line.
function varargout = ShowScanGUI_OutputFcn(hObject, eventdata, handles) 
% varargout  cell array for returning output args (see VARARGOUT);
% hObject    handle to figure
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Get default command line output from handles structure
varargout{1} = handles.output;


% --- Executes on slider movement.
function slider1_Callback(hObject, eventdata, handles)
% hObject    handle to slider1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'Value') returns position of slider
%        get(hObject,'Min') and get(hObject,'Max') to determine range of slider
if handles.loaded == 2

nsel = round(get(hObject,'Value'));
handles.nsel = nsel;

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end

if handles.scale == 0
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    plot(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
    ylim([0 2e6]);
else
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    semilogy(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
    %ylim([(handles.minval)-0.1 (handles.maxval)+0.1])
end

if handles.roi == 1

    axes(handles.axes5);
    dataflp = flip(handles.data, 2);
    pcolor((dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,handles.nsel))');
    clear dataflp
    shading flat
    colorbar
    
    axes(handles.axes1)
    rectangle('Position',[handles.roix,handles.roiy,handles.roiwidth,handles.roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')
end

% axes(handles.axes4)
% dataint = sum(sum(handles.data(:,:,:),2),1);
% scatter(handles.delay, dataint, '.k');
% xlabel('Piezo position (um)');
% ylabel('Intensity (arb.u.)');
% xlim(hhg_analysis_limits(handles.delay));

set(handles.text11, 'String', [num2str(nsel) '/' num2str(handles.flength)]);
set(handles.text7, 'String', ['Current delay: ' num2str(handles.delay(nsel), '%10.2f') ' um']);

end

% Update handles structure
guidata(hObject, handles);

% --- Executes during object creation, after setting all properties.
function slider1_CreateFcn(hObject, eventdata, handles)
% hObject    handle to slider1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: slider controls usually have a light gray background.
if isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor',[.9 .9 .9]);
end

% --- Executes on button press in pushbutton1.
function pushbutton1_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
[FileName,PathName] = uigetfile('*.txt','Select the scan TXT file');
if FileName ~= 0
    
fullfilename = [PathName FileName];
nsel = 1;

text = importdata(fullfilename, '_');
    
    text = importdata([fullfilename(1:end-4) '.txt'], '_');
    if size(text.data, 2) == 2
        width = text.data(1);
        height = text.data(2);
    elseif size(text.data, 2) == 3
        width = text.data(2);
        height = text.data(3); 
    end
    
disp(['Loaded txt file ' fullfilename]);

%search for delay files
fullfilenamestart = fullfilename(1:end-4);
%filenamestart = FileName;
filenamestart = FileName(1:end-4);
fsearch = [fullfilenamestart '*.bin'];
flist = dir(fsearch);
flength = size(flist,1);
if nsel > flength
    nsel = flength;
end

clear data
clear handles.data
data = zeros(width, height, flength, 'uint16');

delay2 = zeros(flength, 2);
%extract delays from data and sort them
for n = 1:flength
    f = flist(n).name;
    %disp(f(size(filenamestart,2)+1:end-4));
    delay2(n,1) = str2double(f(size(filenamestart,2)+2:end-4));
    delay2(n,2) = n;
end
delay2 = sortrows(delay2);
% for n = 1:flength
%     disp([num2str(delay2(n,1)) '  ' num2str(delay2(n,2))]);
% end

try
    handles.bg = evalin('base', 'bg') * 0.95; %
catch err
    handles.bg = 0;
end
if handles.bg == 0
    disp('No background data (name: bg) in workspace.');
else
    disp('Loaded background data (name: bg) from workspace.');
end

%extract data from the files
for n2 = 1:flength
    n = delay2(n2, 2);
    f = flist(n).name;
    %disp(f(size(filenamestart,2)+1:end-4));
    delay(n2) = str2double(f(size(filenamestart,2)+2:end-4));
    disp(['Loading ' f ', delay ' num2str(delay(n2)) 'um']);
    fileID = fopen([PathName f]);
    if fileID ~= -1
        O = fread(fileID, [width height], 'uint32=>uint16', 0, 'b');
        O(1,1) = min(min(O));
        fclose(fileID);
    else
        disp('fopen failed');
    end
    O = O - handles.bg;  %background subtraction
    O(O < 0) = 0;
    data(:,:,n2) = O;
    clear O
end
%data = fliplr(data);
if width == 1392 && height == 1040
    data2 = data;
    clear data
    data = data2(80:80+1250-1, 480:480+185-1, :);
    width = 1250;
    height = 185;
end
clear data2
for n = 1:flength
    minvals(n) = double(min(min(data(:,:,n))));
    maxvals(n) = double(max(max(data(:,:,n))));
end

x = (1:width)';
y = (height:-1:1);
maxval = max(maxvals);
%maxval
minval = min(minvals)+31;
disp(['Loaded ' num2str(n) ' data files']);

if handles.scale == 0
    axes(handles.axes1)
    pcolor(x,y,data(:,:,nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(x,y,log10(double(data(:,:,nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(minval) log10(maxval)])
    colorbar
end

if handles.scale == 0
    axes(handles.axes2)
    datainty = sum(data(:,:,nsel),2);
    plot(x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 width]);
    %ylim([0 maxval]);
else
    axes(handles.axes2)
    datainty = sum(data(:,:,nsel),2);
    semilogy(x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 width]);
    %ylim([(minval)-0.1 (maxval)+0.1])
end

if handles.roi == 0
    axes(handles.axes3)
    dataint = squeeze(sum(sum(data(:,:,:),2),1));
    plot(squeeze(delay), dataint, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(delay));
else
    axes(handles.axes1)
    rectangle('Position',[handles.roix,handles.roiy,handles.roiwidth,handles.roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')
    axes(handles.axes3);
    dataflp = flip(data, 2);
    roidata = squeeze(sum(sum(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,:),2),1));
    plot(squeeze(delay), roidata, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(delay));
    
    axes(handles.axes5);
    pcolor((dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,nsel))');
    shading flat
    colorbar
end

set(handles.text7, 'String', ['Current delay: ' num2str(delay(nsel), '%10.2f') ' um']);
set(handles.text2, 'String', [FileName]);
set(handles.text15, 'String', strcat(text.textdata(4), '->', text.textdata(5), ', ', text.textdata(6), 'ms, g', ...
    text.textdata(7), ', ', text.textdata(8), 'kV, ', text.textdata(9), 'W'));

set(handles.slider1, 'Enable', 'on');
set(handles.pushbutton2, 'Enable', 'on');
set(handles.pushbutton3, 'Enable', 'on');
set(handles.radiobutton1, 'Enable', 'on');
set(handles.radiobutton2, 'Enable', 'on');
set(handles.text11, 'Visible', 'on');
set(handles.text11, 'String', [num2str(nsel) '/' num2str(flength)]);

set(handles.slider1, 'Value', 1);
set(handles.slider1, 'Min', 1);
set(handles.slider1, 'Max', max(2,flength));
set(handles.slider1, 'SliderStep', [1/max(1,flength) 1/max(1,flength)]);

handles.delay = delay;
handles.flength = flength;
handles.width = width;
handles.height = height;
handles.data = data;
handles.nsel = nsel;
handles.x = x;
handles.y = y;
handles.maxval = maxval;
handles.minval = minval;
handles.roix = 1;
handles.roiy = 1;
handles.roiwidth = 50;
handles.roiheight = 50;
handles.roi = 0;
handles.loaded = 2;
handles.filename = FileName;
if ~isfield(handles,'hhg_load_serial'), handles.hhg_load_serial = 0; end
handles.hhg_load_serial = handles.hhg_load_serial + 1;

clear O
clear data

else
    disp('User canceled file dialog.');
end

% Update handles structure
guidata(hObject, handles);

% --- Executes on button press in radiobutton1.
function radiobutton1_Callback(hObject, eventdata, handles)
% hObject    handle to radiobutton1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

rstate = get(hObject, 'Value');
if rstate == 1
    set(handles.radiobutton2, 'Value', 0);
else
    set(handles.radiobutton2, 'Value', 1);
end
handles.scale = 0;

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    %caxis([log10(handles.minval) log10(handles.maxval)])
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end

if handles.scale == 0
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    plot(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
else
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    semilogy(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
    %ylim([(handles.minval)-0.1 (handles.maxval)+0.1])
end

% Update handles structure
guidata(hObject, handles);

% --- Executes on button press in radiobutton2.
function radiobutton2_Callback(hObject, eventdata, handles)
% hObject    handle to radiobutton2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

rstate = get(hObject, 'Value');
if rstate == 1
    set(handles.radiobutton1, 'Value', 0);
else
    set(handles.radiobutton1, 'Value', 1);
end
handles.scale = 1;

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel))' + 0.1));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    %caxis([log10(handles.minval) log10(handles.maxval)])
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end

if handles.scale == 0
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    plot(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
else
    axes(handles.axes2)
    datainty = sum(handles.data(:,:,handles.nsel),2);
    semilogy(handles.x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 handles.width]);
    %ylim([4e4 2e6])
end

% Update handles structure
guidata(hObject, handles);

% --- Executes on button press in pushbutton2.
function pushbutton2_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end
zoom off

axes(handles.axes1);
rect = round(getrect);
handles.roi = 1;
handles.roix = rect(1);
handles.roiy = rect(2);
handles.roiwidth = rect(3);
handles.roiheight = rect(4);
if handles.roiwidth < 1 
    handles.roiwidth = 1;
end
if handles.roiheight < 1 
    handles.roiheight = 1;
end
rectangle('Position',[handles.roix,handles.roiy,handles.roiwidth,handles.roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')

set(handles.text8, 'String', ['x ' num2str(handles.roix) ', y ' num2str(handles.roiy)]);
set(handles.text10, 'String', ['w ' num2str(handles.roiwidth) ', h ' num2str(handles.roiheight)]);

if handles.loaded == 2
    axes(handles.axes3);
    dataflp = flip(handles.data, 2);
    roidata = squeeze(sum(sum(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,:),2),1));
    clear dataflp
    plot(squeeze(handles.delay), roidata, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(handles.delay));
    %xlim([16 20]);
    set(handles.text17, 'String', ['Total avrg. ROI counts:  ' num2str(sum(sum(sum(roidata)))/handles.flength)]);
    clear roidata
    
%     axes(handles.axes5);
%     pcolor((dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,handles.nsel))');
    shading flat
    colorbar
    
else
    dataflp = flip(handles.data, 2);
    set(handles.text17, 'String', ['Total ROI counts:  ' num2str(sum(sum(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,1))))]);   
    clear dataflp
end

% Update handles structure
guidata(hObject, handles);

% --- Executes on button press in pushbutton3.
function pushbutton3_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton3 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end

if handles.loaded == 2

    handles.roi = 0;
    axes(handles.axes3)
    dataint = squeeze(sum(sum(handles.data(:,:,:),2),1));
    plot(squeeze(handles.delay), dataint, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(handles.delay));

end

set(handles.text8, 'String', 'ROI: none');
set(handles.text10, 'String', '  ');
set(handles.text17, 'String', ['  ']);

% Update handles structure
guidata(hObject, handles);


% --- Executes on button press in pushbutton4.
function pushbutton4_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton4 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton5.
function pushbutton5_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton5 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton6.
function pushbutton6_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton6 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton7.
function pushbutton7_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton7 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton8.
function pushbutton8_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton8 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton9.
function pushbutton9_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton9 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton10.
function pushbutton10_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton10 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton11.
function pushbutton11_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton11 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton12.
function pushbutton12_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton12 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton13.
function pushbutton13_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton13 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

[FileName,PathName] = uigetfile('*.pic','Select the image PIC file');
if FileName ~= 0
    fullfilename = [PathName FileName];
    nsel = 1;
    flength = 1;
    
try
    handles.bg = evalin('base', 'bg') * 0.95; %
catch err
    handles.bg = 0;
end
if handles.bg == 0
    disp('No background data (name: bg) in workspace.');
else
    disp('Loaded background data (name: bg) from workspace.');
end
    
    [fullfilename(1:end-4) '.txt'];
    
    text = importdata([fullfilename(1:end-4) '.txt'], '_');
    if size(text.data, 2) == 2
        width = text.data(1);
        height = text.data(2);
    elseif size(text.data, 2) == 3
        width = text.data(2);
        height = text.data(3); 
    end
    
    disp(['Loaded txt file ' fullfilename]);
    
    fileID = fopen(fullfilename);
    
    if fileID ~= -1
        clear data
        clear handles.data
        data = zeros(width, height, flength, 'uint16');
        O = fread(fileID, [width height], 'uint32=>uint16', 0, 'b');
        fclose(fileID);
        O = O - handles.bg;  %background subtraction
        O(O < 0) = 0;
        data(:,:,nsel) = O;
    else
        disp('fopen failed');
    end
    
    disp(['Loaded pic file ' fullfilename]);  
    if width == 1392 && height == 1040
        data2 = data;
        clear data
        data = data2(80:80+1250-1, 480:480+185-1, :);
        width = 1250;
        height = 185;
    end
    maxval = double(max(max(data(:,:,nsel))));
    minval = double(min(min(data(:,:,nsel))) + 11);
    
    x = (1:width)';
    y = (height:-1:1);
       
    if handles.scale == 0
        axes(handles.axes1)
        pcolor(x,y,data(:,:,nsel)');
        xlabel('x (px)');
        ylabel('y (px)');
        shading flat
        caxis([0 maxval])
        colorbar
    else
        axes(handles.axes1)
        pcolor(x,y,log10(double(data(:,:,nsel)' + 0.1)));
        xlabel('x (px)');
        ylabel('y (px)');
        shading flat
        caxis([log10(minval) log10(maxval)])
        colorbar
    end
    
    if handles.scale == 0
        axes(handles.axes2)
        datainty = sum(data(:,:,nsel),2);
        plot(x,datainty,'LineWidth',1.5);
        xlabel('x (px)');
        ylabel('Intensity (arb.u.)');
        xlim([1 width]);
    else
        axes(handles.axes2)
        datainty = sum(data(:,:,nsel),2);
        semilogy(x,datainty,'LineWidth',1.5);
        xlabel('x (px)');
        ylabel('Intensity (arb.u.)');
        xlim([1 width]);
        %ylim([(minval)-0.1 (maxval)+0.1])
    end
    
    set(handles.text7, 'String', ['Current delay: 0 um']);
    set(handles.text2, 'String', [FileName]);
    
    set(handles.slider1, 'Enable', 'off');
    set(handles.pushbutton2, 'Enable', 'on');
    set(handles.pushbutton3, 'Enable', 'on');
    set(handles.text11, 'Visible', 'on');
    set(handles.text11, 'String', '1/1');
    
    handles.delay = 0;
    handles.flength = 1;
    handles.width = width;
    handles.height = height;
    handles.data = data;
    handles.nsel = 1;
    handles.x = x;
    handles.y = y;
    handles.maxval = maxval;
    handles.minval = minval;
    handles.loaded = 1;
    handles.filename = FileName;
if ~isfield(handles,'hhg_load_serial'), handles.hhg_load_serial = 0; end
handles.hhg_load_serial = handles.hhg_load_serial + 1;
else
    disp('User canceled file dialog.');
end

% Update handles structure
guidata(hObject, handles);


% --- Executes on slider movement.
function slider2_Callback(hObject, eventdata, handles)
% hObject    handle to slider2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'Value') returns position of slider
%        get(hObject,'Min') and get(hObject,'Max') to determine range of slider


% --- Executes during object creation, after setting all properties.
function slider2_CreateFcn(hObject, eventdata, handles)
% hObject    handle to slider2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: slider controls usually have a light gray background.
if isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor',[.9 .9 .9]);
end


% --- Executes on button press in checkbox1.
function checkbox1_Callback(hObject, eventdata, handles)
% hObject    handle to checkbox1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkbox1
val = get(hObject, 'Value');
if val == 1
    zoom on
else
    zoom out
    zoom off
end


% --- Executes on button press in radiobutton3.
function radiobutton3_Callback(hObject, eventdata, handles)
% hObject    handle to radiobutton3 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of radiobutton3


% --- Executes on button press in pushbutton14.
function pushbutton14_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton14 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
[FileName,PathName] = uiputfile('*.png','Save to PNG');
if FileName ~= 0
    fullfilename = [PathName FileName];
    exportapp(ancestor(hObject,'figure'), fullfilename);
end

% --- Executes on button press in pushbutton15.
function pushbutton15_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton15 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
assignin('base', 'delay', squeeze(handles.delay));
assignin('base', 'data', handles.data);
assignin('base', 'filename', handles.filename);

if handles.roi == 1
    dataflp = flip(handles.data, 2);
    roidata = squeeze(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,:));
    assignin('base', 'roidata', roidata);
    roidataint = squeeze(sum(sum(roidata,1),2));
    assignin('base', 'roidataint', roidataint);
    assignin('base', 'roix', handles.roix);
    assignin('base', 'roiy', handles.roiy);
    assignin('base', 'roiwidth', handles.roiwidth);
    assignin('base', 'roiheight', handles.roiheight);
end

if handles.loaded == 1
    lineout = squeeze(sum(handles.data, 2));
    assignin('base', 'lineout', lineout - min(lineout));
end

% --- Executes on button press in pushbutton16.
function pushbutton16_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton16 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


if handles.roi == 1
    assignin('base', 'delay', squeeze(handles.delay));
    %assignin('base', 'data', handles.data);
    assignin('base', 'filename', handles.filename);
    dataflp = flip(handles.data, 2);
    roidata = squeeze(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,:));
    clear dataflp
    assignin('base', 'roidata', roidata);
    assignin('base', 'roix', handles.roix);
    assignin('base', 'roiy', handles.roiy);
    assignin('base', 'roiwidth', handles.roiwidth);
    assignin('base', 'roiheight', handles.roiheight);
    ShowScanROIFFTGUI();
end


% --- Executes on button press in checkbox2.
function checkbox2_Callback(hObject, eventdata, handles)
% hObject    handle to checkbox2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkbox2
val = get(hObject, 'Value');
if val == 1
    datacursormode on
else
    datacursormode off
end

% --- Executes on button press in pushbutton17.
function pushbutton17_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton17 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

binnr = 4;
try
    binnr = hhg_analysis_bin_size(get(handles.edit1, 'String'));
    if mod(binnr,2) ~= 0 && binnr > 1
        binnr = 4;
    end
catch err
    %
end

binwidth = floor(handles.width / binnr);
binheight = floor(handles.height / binnr);
bindata = zeros(binwidth, binheight, handles.flength, 'uint16');
% binwidth
% binheight
% size(bindata)
for j = 1:binwidth
    for k = 1:binheight
%         disp(num2str(1+(k-1)*binnr));
%         disp(num2str(k*binnr));
        bindata(j, k, :) = sum(sum(handles.data(1+(j-1)*binnr:j*binnr, 1+(k-1)*binnr:k*binnr, :),1),2);
    end
end

assignin('base', 'delay', squeeze(handles.delay));
assignin('base', 'bindata', bindata);
assignin('base', 'filename', handles.filename);
assignin('base', 'binnr', binnr);
assignin('base', 'binwidth', binwidth);
assignin('base', 'binheight', binheight);
evalin('base',['clear ','roix']);
evalin('base',['clear ','roiy']);
evalin('base',['clear ','roiwidth']);
evalin('base',['clear ','roiheight']);

clear bindata

ShowScanPxFFTGUI();


% --- Executes on button press in pushbutton18.
function pushbutton18_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton18 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- Executes on button press in pushbutton19.
function pushbutton19_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton19 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
if handles.loaded == 1
    assignin('base', 'bg', handles.data);
else
    assignin('base', 'bg', handles.data(:,:,handles.nsel));
end
    
% --- Executes on button press in pushbutton20.
function pushbutton20_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton20 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
assignin('base', 'bg', 0);


% --- Executes on button press in pushbutton21.
function pushbutton21_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton21 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
[FileName,PathName] = uigetfile('*.msk','Select the harmonic mask file');
% if ispc == 1
%     sep = '\';
% else
%     sep = '/';
% end
if FileName ~= 0
    fullfilename = [PathName FileName];
    disp(PathName);
    %[fullfilename(1:end-4) '.msk'];
    
    O = importdata([fullfilename(1:end-4) '.msk']);
    
    nharm = size(O, 1);
    for m = 1:nharm
        H(m) = O(m, 1);
        roix = O(m, 2);
        roiy = O(m, 3);
        roiwidth = O(m, 4);
        roiheight = O(m, 5);
        dataflp = flip(handles.data, 2);
        roidata = double(squeeze(sum(sum(dataflp(roix:roix + roiwidth-1,roiy:roiy+roiheight-1,:),2),1)));
        clear dataflp
        Havrg(m) = sum(sum(sum(roidata)))/handles.flength;
        disp(['Saving ' handles.filename(1:end-4) '__' FileName(1:end-4) '__H' num2str(H(m)) '.csv']);
        out = [(squeeze(handles.delay*2))' roidata];
        %size(out);
        if handles.loaded == 2
            hhg_analysis_write_csv(([PathName handles.filename(1:end-4) '__' FileName(1:end-4) '__H' num2str(H(m)) '.csv']), out); %, 'precision','%.7f'
        end
    end
    out = [H' Havrg'];
    disp(['Saving ' handles.filename(1:end-4) '__' FileName(1:end-4) '__H_avrgYield.csv']);
    hhg_analysis_write_csv(([PathName handles.filename(1:end-4) '__' FileName(1:end-4) '__H_avrgYield.csv']), out); %, 'precision','%.7f'
    %size(out)
else
    disp('User canceled file dialog.');
end


% --- Executes on button press in pushbutton22.
function pushbutton22_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton22 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
[FileName,PathName] = uigetfile('*.tcs','Select the scan TCS file');
if FileName ~= 0
    
fullfilename = [PathName FileName];
nsel = 1;

text = importdata(fullfilename, '_');
    
    text = importdata([fullfilename(1:end-4) '.tcs'], '_');
    if size(text.data, 2) == 2
        width = text.data(1);
        height = text.data(2);
    elseif size(text.data, 2) == 3
        width = text.data(2);
        height = text.data(3); 
    end
    
disp(['Loaded tcs file ' fullfilename]);

%search for delay files
fullfilenamestart = fullfilename(1:end-4);
%filenamestart = FileName;
filenamestart = FileName(1:end-4);
fsearch = [fullfilenamestart '*.bin'];
flist = dir(fsearch);
flength = size(flist,1);
if nsel > flength
    nsel = flength;
end

clear data
clear handles.data
data = zeros(width, height, flength, 'uint16');

delay2 = zeros(flength, 2);
%extract delays from data and sort them
for n = 1:flength
    f = flist(n).name;
    mx = 14; %new TCD format
    % mx = 10; %old format
    disp(f(size(filenamestart,2)+2:end-4-mx));
    delay2(n,1) = str2double(f(size(filenamestart,2)+2:end-4-mx));
    delay2(n,2) = n;
end
delay2 = sortrows(delay2);
% for n = 1:flength
%     disp([num2str(delay2(n,1)) '  ' num2str(delay2(n,2))]);
% end

try
    handles.bg = evalin('base', 'bg') * 0.95; %
catch err
    handles.bg = 0;
end
if handles.bg == 0
    disp('No background data (name: bg) in workspace.');
else
    disp('Loaded background data (name: bg) from workspace.');
end

%extract data from the files
for n2 = 1:flength
    n = delay2(n2, 2);
    f = flist(n).name;
    %disp(f(size(filenamestart,2)+1:end-4));
    delay(n2) = str2double(f(size(filenamestart,2)+2:end-4-mx));
    disp(['Loading ' f ', delay ' num2str(delay(n2)) 'mm']);
    fileID = fopen([PathName f]);
    if fileID ~= -1
        O = fread(fileID, [width height], 'uint32=>uint16', 0, 'b');
        O(1,1) = min(min(O));
        fclose(fileID);
    else
        disp('fopen failed');
    end
    O = O - handles.bg;  %background subtraction
    O(O < 0) = 0;
    data(:,:,n2) = O;
    clear O
end
%data = fliplr(data);
if width == 1392 && height == 1040
    data2 = data;
    clear data
    data = data2(80:80+1250-1, 480:480+185-1, :);
    width = 1250;
    height = 185;
end
clear data2
for n = 1:flength
    minvals(n) = min(min(data(:,:,n)));
    maxvals(n) = max(max(data(:,:,n)));
end

x = (1:width)';
y = (height:-1:1);
maxval = double(max(maxvals));
%maxval
minval = double(min(minvals)+31);
disp(['Loaded ' num2str(n) ' data files']);

if handles.scale == 0
    axes(handles.axes1)
    pcolor(x,y,data(:,:,nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(x,y,log10(double(data(:,:,nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(minval) log10(maxval)])
    colorbar
end

if handles.scale == 0
    axes(handles.axes2)
    datainty = sum(data(:,:,nsel),2);
    plot(x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 width]);
    %ylim([0 maxval]);
else
    axes(handles.axes2)
    datainty = sum(data(:,:,nsel),2);
    semilogy(x,datainty,'LineWidth',1.5);
    xlabel('x (px)');
    ylabel('Intensity (arb.u.)');
    xlim([1 width]);
    %ylim([(minval)-0.1 (maxval)+0.1])
end

if handles.roi == 0
    axes(handles.axes3)
    dataint = squeeze(sum(sum(data(:,:,:),2),1));
    plot(squeeze(delay), dataint, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(delay));
else
    axes(handles.axes1)
    rectangle('Position',[handles.roix,handles.roiy,handles.roiwidth,handles.roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')
    axes(handles.axes3);
    dataflp = flip(data, 2);
    roidata = double(squeeze(sum(sum(dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,:),2),1)));
    plot(squeeze(delay), roidata, '-k*');
    xlabel('Piezo position (um)');
    ylabel('Intensity (arb.u.)');
    xlim(hhg_analysis_limits(delay));
    
    axes(handles.axes5);
    pcolor((dataflp(handles.roix:handles.roix + handles.roiwidth-1,handles.roiy:handles.roiy+handles.roiheight-1,nsel))');
    shading flat
    colorbar
end

set(handles.text7, 'String', ['Current delay: ' num2str(delay(nsel), '%10.2f') ' um']);
set(handles.text2, 'String', [FileName]);
set(handles.text15, 'String', strcat(text.textdata(4), '->', text.textdata(5), ', ', text.textdata(6), 'ms, g', ...
    text.textdata(7), ', ', text.textdata(8), 'kV, ', text.textdata(9), 'W'));

set(handles.slider1, 'Enable', 'on');
set(handles.pushbutton2, 'Enable', 'on');
set(handles.pushbutton3, 'Enable', 'on');
set(handles.radiobutton1, 'Enable', 'on');
set(handles.radiobutton2, 'Enable', 'on');
set(handles.text11, 'Visible', 'on');
set(handles.text11, 'String', [num2str(nsel) '/' num2str(flength)]);

set(handles.slider1, 'Value', 1);
set(handles.slider1, 'Min', 1);
set(handles.slider1, 'Max', max(2,flength));
set(handles.slider1, 'SliderStep', [1/max(1,flength) 1/max(1,flength)]);

handles.delay = delay;
handles.flength = flength;
handles.width = width;
handles.height = height;
handles.data = data;
handles.nsel = nsel;
handles.x = x;
handles.y = y;
handles.maxval = maxval;
handles.minval = minval;
handles.roix = 1;
handles.roiy = 1;
handles.roiwidth = 50;
handles.roiheight = 50;
handles.roi = 0;
handles.loaded = 2;
handles.filename = FileName;
if ~isfield(handles,'hhg_load_serial'), handles.hhg_load_serial = 0; end
handles.hhg_load_serial = handles.hhg_load_serial + 1;

clear O
clear data

else
    disp('User canceled file dialog.');
end

% Update handles structure
guidata(hObject, handles);



function edit1_Callback(hObject, eventdata, handles)
% hObject    handle to edit1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of edit1 as text
%        str2double(get(hObject,'String')) returns contents of edit1 as a double


% --- Executes during object creation, after setting all properties.
function edit1_CreateFcn(hObject, eventdata, handles)
% hObject    handle to edit1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end


% --- Executes on button press in checkbox3.
function checkbox3_Callback(hObject, eventdata, handles)
% hObject    handle to checkbox3 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkbox3


% --- Executes on button press in pushbutton23.
function pushbutton23_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton23 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

binnr = 4;
try
    binnr = hhg_analysis_bin_size(get(handles.edit1, 'String'));
    if mod(binnr,2) ~= 0 && binnr > 1
        binnr = 4;
    end
catch err
    %
end

if handles.scale == 0
    axes(handles.axes1)
    pcolor(handles.x,handles.y,handles.data(:,:,handles.nsel)');
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([0 handles.maxval])
    colorbar
else
    axes(handles.axes1)
    pcolor(handles.x,handles.y,log10(double(handles.data(:,:,handles.nsel)' + 0.1)));
    xlabel('x (px)');
    ylabel('y (px)');
    shading flat
    caxis([log10(handles.minval) log10(handles.maxval)])
    colorbar
end
zoom off

axes(handles.axes1);
rect = round(getrect);
roix = rect(1);
roiy = rect(2);
roiwidth = rect(3);
roiheight = rect(4);

set(handles.text8, 'String', ['x ' num2str(roix) ', y ' num2str(roiy)]);
set(handles.text10, 'String', ['w ' num2str(roiwidth) ', h ' num2str(roiheight)]);

axes(handles.axes1);
rectangle('Position',[roix,roiy,roiwidth,roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')

dataflp = flip(handles.data, 2);
roidata = squeeze(dataflp(roix:roix + roiwidth-1,roiy:roiy+roiheight-1,:));
roidata = flip(roidata, 2);
clear dataflp

binwidth = floor(roiwidth / binnr);
binheight = floor(roiheight / binnr);
bindata = zeros(binwidth, binheight, handles.flength, 'uint16');

for j = 1:binwidth
    for k = 1:binheight
%         disp(num2str(1+(k-1)*binnr));
%         disp(num2str(k*binnr));
        bindata(j, k, :) = mean(mean(roidata(1+(j-1)*binnr:j*binnr, 1+(k-1)*binnr:k*binnr, :),1),2);
    end
end

assignin('base', 'delay', squeeze(handles.delay));
assignin('base', 'bindata', bindata);
assignin('base', 'filename', handles.filename);
assignin('base', 'binnr', binnr);
assignin('base', 'binwidth', binwidth);
assignin('base', 'binheight', binheight);
assignin('base', 'roix', roix);
assignin('base', 'roiy', roiy);
assignin('base', 'roiwidth', roiwidth);
assignin('base', 'roiheight', roiheight);

ShowScanPxFFTGUI();

guidata(hObject, handles);

% --- Executes on button press in pushbutton24.
function pushbutton24_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton24 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

binnr = 4;
try
    binnr = hhg_analysis_bin_size(get(handles.edit1, 'String'));
    if mod(binnr,2) ~= 0 && binnr > 1
        binnr = 4;
    end
catch err
    %
end

roix = 374;
roiy = 77;
roiwidth = 593;
roiheight = 42;

axes(handles.axes1);
rectangle('Position',[roix,roiy,roiwidth,roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')

dataflp = flip(handles.data, 2);
roidata = squeeze(dataflp(roix:roix + roiwidth-1,roiy:roiy+roiheight-1,:));
roidata = flip(roidata, 2);
clear dataflp

binwidth = floor(roiwidth / binnr);
binheight = floor(roiheight / binnr);
bindata = zeros(binwidth, binheight, handles.flength, 'uint16');

for j = 1:binwidth
    for k = 1:binheight
%         disp(num2str(1+(k-1)*binnr));
%         disp(num2str(k*binnr));
        bindata(j, k, :) = sum(sum(roidata(1+(j-1)*binnr:j*binnr, 1+(k-1)*binnr:k*binnr, :),1),2);
    end
end

assignin('base', 'delay', squeeze(handles.delay));
assignin('base', 'bindata', bindata);
assignin('base', 'filename', handles.filename);
assignin('base', 'binnr', binnr);
assignin('base', 'binwidth', binwidth);
assignin('base', 'binheight', binheight);
assignin('base', 'roix', roix);
assignin('base', 'roiy', roiy);
assignin('base', 'roiwidth', roiwidth);
assignin('base', 'roiheight', roiheight);

ShowScanPxFFTGUI();

guidata(hObject, handles);


% --- Executes on button press in pushbutton25.
function pushbutton25_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton25 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

if handles.maskfile == 0
    [FileName, PathName] = uigetfile('*.msk','Select the harmonic mask file');
    if FileName ~= 0
        handles.maskfile = FileName;
        handles.maskpath = PathName;
        set(handles.edit3, 'String', FileName);
    end
else
    FileName = handles.maskfile;
    PathName = handles.maskpath;
end
% if ispc == 1
%     sep = '\';
% else
%     sep = '/';
% end

if FileName ~= 0
    if handles.loaded == 2
        fullfilename = [PathName FileName];
        disp(PathName);
        %[fullfilename(1:end-4) '.msk'];
        
        O = importdata([fullfilename(1:end-4) '.msk']);
        prefix = get(handles.edit2, 'String');

        axes(handles.axes1);
        nharm = size(O, 1);
        for m = 1:nharm
            H(m) = O(m, 1);
            disp(['Loading H' num2str(H(m))]);
            roix = O(m, 2);
            roiy = O(m, 3);
            roiwidth = O(m, 4);
            roiheight = O(m, 5);
            rectangle('Position',[roix,roiy,roiwidth,roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')
            dataflp = flip(handles.data, 2);
            roidata = flip(squeeze(dataflp(roix:roix + roiwidth-1,roiy:roiy+roiheight-1,:)), 2);
            out2 = flip(permute(roidata, [2 1 3]),1);
            % Save raw data
            assignin('base', matlab.lang.makeValidName([prefix '_H' num2str(H(m)) 'data']), out2);
            clear dataflp
            clear roidata
%             % Px-by-Px FFT analysis
%             for nx = 1:roiwidth
%                 for ny = 1:roiheight
%                     
%                 end
%             end
        end
        piezodelay = handles.delay;
        assignin('base', [prefix '_piezoaxis'], piezodelay);
        assignin('base', [prefix '_Hnumbase'], H);
        assignin('base', [prefix '_filename'], handles.filename);
    end
else
    disp('User canceled file dialog.');
end

guidata(hObject, handles);


function edit2_Callback(hObject, eventdata, handles)
% hObject    handle to edit2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of edit2 as text
%        str2double(get(hObject,'String')) returns contents of edit2 as a double


% --- Executes during object creation, after setting all properties.
function edit2_CreateFcn(hObject, eventdata, handles)
% hObject    handle to edit2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end



function edit3_Callback(hObject, eventdata, handles)
% hObject    handle to edit3 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of edit3 as text
%        str2double(get(hObject,'String')) returns contents of edit3 as a double


% --- Executes during object creation, after setting all properties.
function edit3_CreateFcn(hObject, eventdata, handles)
% hObject    handle to edit3 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end


% --- Executes on button press in pushbutton26.
function pushbutton26_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton26 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

[FileName, PathName] = uigetfile('*.msk','Select the harmonic mask file');
if FileName ~= 0
    handles.maskfile = FileName;
    handles.maskpath = PathName;
    set(handles.edit3, 'String', FileName);
end

guidata(hObject, handles);

% --- Executes on button press in pushbutton27.
function pushbutton27_Callback(hObject, eventdata, handles)
% hObject    handle to pushbutton27 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


if handles.maskfile == 0
    [FileName, PathName] = uigetfile('*.msk','Select the harmonic mask file');
    if FileName ~= 0
        handles.maskfile = FileName;
        handles.maskpath = PathName;
        set(handles.edit3, 'String', FileName);
    end
else
    FileName = handles.maskfile;
    PathName = handles.maskpath;
end
% if ispc == 1
%     sep = '\';
% else
%     sep = '/';
% end

if FileName ~= 0
    if handles.loaded == 2
        fullfilename = [PathName FileName];
        disp(PathName);
        %[fullfilename(1:end-4) '.msk'];
        
        O = importdata([fullfilename(1:end-4) '.msk']);
        prefix = get(handles.edit2, 'String');

        axes(handles.axes1);
        nharm = size(O, 1);
        for m = 1:nharm
            H(m) = O(m, 1);
            disp(['Loading H' num2str(H(m))]);
            roix = O(m, 2);
            roiy = O(m, 3);
            roiwidth = O(m, 4);
            roiheight = O(m, 5);
            rectangle('Position',[roix,roiy,roiwidth,roiheight], 'LineWidth', 1, 'Clipping', 'on', 'EdgeColor', 'w')
            dataflp = flip(handles.data, 2);
            roidata = flip(squeeze(dataflp(roix:roix + roiwidth-1,roiy:roiy+roiheight-1,:)), 2);
            out2 = flip(permute(roidata, [2 1 3]),1);
            % Save raw data
            assignin('base', matlab.lang.makeValidName([prefix '_H' num2str(H(m)) 'data']), out2);
            clear dataflp
            clear roidata
%             % Px-by-Px FFT analysis
%             for nx = 1:roiwidth
%                 for ny = 1:roiheight
%                     
%                 end
%             end
        end
        piezodelay = handles.delay;
        assignin('base', [prefix '_piezoaxis'], piezodelay);
        assignin('base', [prefix '_Hnumbase'], H);
        assignin('base', [prefix '_filename'], handles.filename);
        assignin('base', 'loadname', prefix);
        ShowScanHHFFTGUI();
    end
else
    disp('User canceled file dialog.');
end

guidata(hObject, handles);


% --- If Enable == 'on', executes on mouse press in 5 pixel border.
% --- Otherwise, executes on mouse press in 5 pixel border or over radiobutton1.
function radiobutton1_ButtonDownFcn(hObject, eventdata, handles)
% hObject    handle to radiobutton1 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% --- If Enable == 'on', executes on mouse press in 5 pixel border.
% --- Otherwise, executes on mouse press in 5 pixel border or over slider2.
function slider2_ButtonDownFcn(hObject, eventdata, handles)
% hObject    handle to slider2 (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
