function [coeff,score,latent,method] = hhg_analysis_pca(observations)
%HHG_ANALYSIS_PCA Centered sample PCA matching the supplied pca(X) use.
% Use MATLAB pca when installed. Base-MATLAB SVD provides the same centered
% sample covariance eigensystem for finite data without Statistics Toolbox.
% Repeated/zero eigenvalues may have different equally valid basis vectors.
% Source: https://www.mathworks.com/help/stats/pca.html
assert(ismatrix(observations)&&size(observations,1)>=2&&all(isfinite(observations),'all'), ...
    'HHG:PCA','PCA requires at least two finite observations; no frames are discarded.');
if ~isempty(which('pca'))
    [coeff,score,latent]=pca(observations);
    method='MATLAB pca: centered SVD, sample covariance (N-1)'; return;
end
n=size(observations,1); p=size(observations,2);
centered=double(observations)-mean(double(observations),1);
[u,s,v]=svd(centered,'econ'); count=min(n-1,p);
coeff=v(:,1:count); score=u(:,1:count)*s(1:count,1:count);
latent=diag(s(1:count,1:count)).^2/(n-1);
% Fix the sign ambiguity using the largest absolute loading per component.
[~,idx]=max(abs(coeff),[],1);
signs=sign(coeff(sub2ind(size(coeff),idx,1:count))); signs(signs==0)=1;
coeff=coeff.*signs; score=score.*signs;
method='Base MATLAB centered SVD, sample covariance (N-1); finite inputs only';
end
