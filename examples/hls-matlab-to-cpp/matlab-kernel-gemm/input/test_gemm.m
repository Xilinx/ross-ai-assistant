%
% Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
% SPDX-License-Identifier: MIT
%
% Author: Mark Rollins

% Test script for gemm.m
% Generates random single-precision matrices A (M x N) and B (N x L),
% computes C = gemm(A, B), and compares against MATLAB's built-in result.

M = 64;
N = 128;
L = 32;

rng(0);
A = rand(M, N, 'single');
B = rand(N, L, 'single');

C = gemm(A, B);
C_ref = A * B;

max_abs_err = max(abs(C(:) - C_ref(:)));

fprintf('A size: [%d x %d], class: %s\n', size(A, 1), size(A, 2), class(A));
fprintf('B size: [%d x %d], class: %s\n', size(B, 1), size(B, 2), class(B));
fprintf('C size: [%d x %d], class: %s\n', size(C, 1), size(C, 2), class(C));
fprintf('Max absolute error vs A*B: %.8g\n', max_abs_err);

assert(isa(A, 'single') && isa(B, 'single') && isa(C, 'single'), ...
    'Expected single-precision inputs and output.');
assert(all(size(C) == [M, L]), 'Output matrix C has unexpected size.');
