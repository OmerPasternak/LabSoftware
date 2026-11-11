function source = hhg_analysis_inspect_hdf5(paths,mode)
%HHG_ANALYSIS_INSPECT_HDF5 Validate the current acquisition schema and metadata.
% No old /entry format or filename-derived delay is accepted. Each file is
% complete, uint16 [frame,y,x], with one JSON metadata record per real frame.
if ischar(paths)||isstring(paths), paths=cellstr(paths); end
if nargin<2, mode='frames'; end
assert(~isempty(paths)&&(~strcmp(mode,'frames')||numel(paths)==1),'HHG:Schema','Frame mode requires exactly one completed scan-step file.');
source=struct('paths',{paths},'mode',mode,'frame_counts',zeros(1,numel(paths)), ...
    'step_indices',zeros(1,numel(paths)),'setpoints',zeros(1,numel(paths)),'metadata',{{}});
for k=1:numel(paths)
    path=paths{k};
    assert(~endsWith(lower(path),'.partial'),'HHG:Incomplete','Partial files are not analysis inputs.');
    complete=h5readatt(path,'/','complete');
    if iscell(complete)&&isscalar(complete), complete=complete{1}; end
    if ischar(complete)||isstring(complete), complete=strcmpi(string(complete),'TRUE'); end
    assert((isnumeric(complete)||islogical(complete))&&isscalar(complete)&&complete==1, ...
        'HHG:Incomplete','The camera file is not complete.');
    assert(strcmp(string(h5readatt(path,'/','schema_version')),'2.0'),'HHG:Schema','Only the current camera schema 2.0 is accepted.');
    info=h5info(path,'/images'); dims=info.Dataspace.Size;
    assert(numel(dims)==3&&all(dims>=1)&&contains(info.Datatype.Type,'U16'), ...
        'HHG:Schema','Expected unsigned 16-bit camera /images with three dimensions.');
    order=string(h5readatt(path,'/images','dimension_order'));
    assert(strcmp(erase(order,' '),'[frame,y,x]'),'HHG:Schema','Unexpected /images dimension_order; refusing an implicit transpose.');
    n=double(h5readatt(path,'/','frames_written'));
    assert(n==dims(3),'HHG:Incomplete','Frame count does not match /images.');
    metadata=h5info(path,'/frame_metadata/json');
    assert(prod(metadata.Dataspace.Size)==n,'HHG:Incomplete','One frame-metadata record is required per image.');
    root=h5info(path); source.metadata{k}=root.Attributes;
    source.frame_counts(k)=n;
    if k==1, source.width=dims(1); source.height=dims(2);
    else, assert(isequal(dims(1:2),[source.width source.height]),'HHG:Schema','Scan-step image geometries differ.'); end
    if strcmp(mode,'step_means')
        source.step_indices(k)=double(h5readatt(path,'/','scan_step_index'));
        source.setpoints(k)=double(h5readatt(path,'/','scan_parameter_value'));
        name=string(h5readatt(path,'/','scan_parameter_name'));
        % Every step uses identical camera geometry/exposure/mode/identity.
        keys={'roi_bounds','exposure_time_s','readout_mode','camera_serial','run_id','experiment_name'};
        for j=1:numel(keys)
            assert(isequal(h5readatt(path,'/',keys{j}),h5readatt(paths{1},'/',keys{j})), ...
                'HHG:Metadata','Incompatible scan-step %s metadata.',keys{j});
        end
        if k==1, source.parameter_name=char(name);
        else, assert(strcmp(name,source.parameter_name),'HHG:Metadata','Scan parameters differ.'); end
    end
end
if strcmp(mode,'step_means')
    assert(numel(unique(source.step_indices))==numel(paths),'HHG:Metadata','Duplicate scan_step_index values; select one file per step rather than silently pooling runs.');
    assert(all(isfinite(source.setpoints)),'HHG:Metadata','Scan positions must be finite.');
    [source.step_indices,idx]=sort(source.step_indices);
    source.paths=source.paths(idx); source.frame_counts=source.frame_counts(idx);
    source.setpoints=source.setpoints(idx); source.metadata=source.metadata(idx);
end
end
