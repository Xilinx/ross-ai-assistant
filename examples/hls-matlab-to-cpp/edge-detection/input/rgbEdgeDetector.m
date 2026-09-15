%Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
%SPDX-License-Identifier: MIT
function [gx, gy, gradMag] = rgbEdgeDetector(img, threshold)
% RGBEDGEDETECTOR - Sobel gradient computation for RGB images
%
% Inputs:
%   img       - RGB image (uint8, uint16, or double)
%   threshold - [UNUSED] Kept for backward compatibility
%
% Outputs:
%   gx        - Sobel X gradient (double)
%   gy        - Sobel Y gradient (double)
%   gradMag   - Gradient magnitude sqrt(gx^2 + gy^2) (double)
%
% Example:
%   img = imread('peppers.png');
%   [gx, gy, mag] = rgbEdgeDetector(img);
%   edges = mag > threshold;

if nargin < 2 || isempty(threshold), threshold = []; end

% Convert to double [0,1]
if ~isa(img, 'double')
    img = im2double(img);
end

% RGB to grayscale (ITU-R BT.601)
if size(img,3) == 3
    gray = 0.299*img(:,:,1) + 0.587*img(:,:,2) + 0.114*img(:,:,3);
else
    gray = img;
end

% Sobel gradients
sobelX = [-1 0 1; -2 0 2; -1 0 1];
sobelY = [-1 -2 -1; 0 0 0; 1 2 1];
gx = imfilter(gray, sobelX, 'replicate', 'conv');
gy = imfilter(gray, sobelY, 'replicate', 'conv');

% Magnitude (L2 norm)
gradMag = sqrt(gx.^2 + gy.^2);

end
