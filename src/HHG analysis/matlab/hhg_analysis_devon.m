function colors = hhg_analysis_devon(fig)
%HHG_ANALYSIS_DEVON Load the bundled Crameri Devon RGB table (MIT license).
colors=readmatrix(fullfile(fileparts(mfilename('fullpath')),'colourmaps','devon.txt'));
assert(isequal(size(colors),[256 3])&&all(colors>=0&colors<=1,'all'), ...
    'HHG:Palette','Bundled Devon color table is invalid.');
if nargin>0, colormap(fig,colors); end
end
