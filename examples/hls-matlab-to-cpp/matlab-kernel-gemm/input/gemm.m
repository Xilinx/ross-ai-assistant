%
% Copyright (C) 2025, Advanced Micro Devices, Inc. All rights reserved.
% SPDX-License-Identifier: MIT
%
% Author: Mark Rollins

function C = gemm(A, B)
%GEMM Multiply matrices A (M x N) and B (N x L).
%
%   C = GEMM(A, B) returns the matrix product C = A * B.

	if ndims(A) ~= 2 || ndims(B) ~= 2
		error('gemm:InputMustBe2D', 'Inputs A and B must be 2-D matrices.');
	end

	if size(A, 2) ~= size(B, 1)
		error('gemm:InnerDimMismatch', ...
			'Inner dimensions must agree: size(A,2) must equal size(B,1).');
	end

	% Built-in vectorized matrix multiplication.
	C = A * B;
end

