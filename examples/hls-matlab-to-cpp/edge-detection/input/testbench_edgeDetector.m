%Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
%SPDX-License-Identifier: MIT
% testbench_edgeDetector.m
% MATLAB testbench for RGB Sobel edge detection
% Generates golden reference data for C++ verification

clear all;
close all;
clc;

fprintf('==============================================\n');
fprintf('MATLAB Edge Detection Testbench\n');
fprintf('==============================================\n\n');

%% 1. Load or create test image
img_file = 'cameraman.png';

% Try to load cameraman from MATLAB or create a test pattern
if exist(img_file, 'file')
    img = imread(img_file);
    fprintf('Loaded: %s\n', img_file);
else
    % Try MATLAB built-in cameraman
    try
        img = imread('cameraman.tif');
        fprintf('Using MATLAB built-in cameraman.tif\n');
    catch
        % Create a simple test pattern if no image available
        fprintf('No test image found - creating synthetic pattern\n');
        img = uint8(randi([0 255], 256, 256));
    end
end

% Ensure it's 256x256 and grayscale (convert to RGB for testing)
if size(img,1) ~= 256 || size(img,2) ~= 256
    fprintf('Resizing to 256x256...\n');
    img = imresize(img, [256 256]);
end

% Convert grayscale to RGB (replicate channels) for testing RGB path
if size(img,3) == 1
    img = repmat(img, [1 1 3]);
end

[ROWS, COLS, CHANNELS] = size(img);
fprintf('Image size: %dx%dx%d (%s)\n', ROWS, COLS, CHANNELS, class(img));

%% 2. Run edge detection
fprintf('\nRunning rgbEdgeDetector...\n');
[gx, gy, gradMag] = rgbEdgeDetector(img);

fprintf('  gx range:      [%.6f, %.6f]\n', min(gx(:)), max(gx(:)));
fprintf('  gy range:      [%.6f, %.6f]\n', min(gy(:)), max(gy(:)));
fprintf('  gradMag range: [%.6f, %.6f]\n', min(gradMag(:)), max(gradMag(:)));

%% 3. Create output directory
output_dir = '../edge_detection_golden_tmp';
if ~exist(output_dir, 'dir')
    mkdir(output_dir);
end

%% 4. Save binary files (column-major, for C++ verification)
fprintf('\nSaving binary files...\n');

% Input (RGB uint8, column-major)
fid = fopen(fullfile(output_dir, 'matlab_input.bin'), 'wb');
fwrite(fid, img(:), 'uint8');  % Already column-major in MATLAB
fclose(fid);
fprintf('  Saved: matlab_input.bin (%d bytes)\n', ROWS*COLS*CHANNELS);

% Gradient X (double, column-major)
fid = fopen(fullfile(output_dir, 'matlab_gx.bin'), 'wb');
fwrite(fid, gx(:), 'double');
fclose(fid);
fprintf('  Saved: matlab_gx.bin (%d bytes)\n', ROWS*COLS*8);

% Gradient Y (double, column-major)
fid = fopen(fullfile(output_dir, 'matlab_gy.bin'), 'wb');
fwrite(fid, gy(:), 'double');
fclose(fid);
fprintf('  Saved: matlab_gy.bin (%d bytes)\n', ROWS*COLS*8);

% Gradient magnitude (double, column-major)
fid = fopen(fullfile(output_dir, 'matlab_golden.bin'), 'wb');
fwrite(fid, gradMag(:), 'double');
fclose(fid);
fprintf('  Saved: matlab_golden.bin (%d bytes)\n', ROWS*COLS*8);

%% 5. Save PNG visualizations
fprintf('\nSaving PNG visualizations...\n');

% Input image
imwrite(img, fullfile(output_dir, 'cameraman.png'));
fprintf('  Saved: cameraman.png\n');

% Edge detection result (threshold for visualization)
threshold = 0.3;  % Adjust based on gradMag range
edges = gradMag > threshold;
imwrite(edges, fullfile(output_dir, 'matlab_edges.png'));
fprintf('  Saved: matlab_edges.png (threshold=%.2f)\n', threshold);

% Gradient magnitude (normalized for visualization)
gradMag_norm = gradMag / max(gradMag(:));
imwrite(gradMag_norm, fullfile(output_dir, 'matlab_gradMag.png'));
fprintf('  Saved: matlab_gradMag.png (normalized)\n');

%% 6. Summary
fprintf('\n==============================================\n');
fprintf('Golden reference generation complete!\n');
fprintf('Output directory: %s\n', output_dir);
fprintf('\nFiles generated:\n');
fprintf('  Binary files (for C++ verification):\n');
fprintf('    - matlab_input.bin  : RGB input (uint8)\n');
fprintf('    - matlab_gx.bin     : Sobel X gradient (double)\n');
fprintf('    - matlab_gy.bin     : Sobel Y gradient (double)\n');
fprintf('    - matlab_golden.bin : Gradient magnitude (double)\n');
fprintf('  PNG files (for visual inspection):\n');
fprintf('    - cameraman.png     : Input image\n');
fprintf('    - matlab_edges.png  : Binary edge map\n');
fprintf('    - matlab_gradMag.png: Gradient magnitude\n');
fprintf('==============================================\n');
