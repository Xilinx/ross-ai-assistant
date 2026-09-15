
%
% Copyright (C) 2025, Advanced Micro Devices, Inc. All rights reserved.
% SPDX-License-Identifier: MIT
%
% Author: Mark Rollins

function [U, S, V, num_sweeps] = svd_jacobi_complex(A,max_sweeps)
    if nargin==1
        max_sweeps = 4;
    end 
    [m, n] = size(A);
    V = eye(n); % Accumulate rotations to get V
    U = A;      % U will eventually be orthogonal columns
    
    tolerance = 1e-6;
    num_sweeps = max_sweeps; % Default to max if not converged

    for sweep = 1:max_sweeps
        converged = true;
        
        % In SIMD, you would use a scheduler to pick (i, j) pairs 
        % that don't overlap (e.g., (1,2), (3,4)...) to run in parallel.
        for i = 1:n-1
            for j = i+1:n
                % --- STEP 1: Compute Dot Products (SIMD Vector Reductions) ---
                % hardware: dot_product(U(:,i), U(:,j))
                g_ii = real(U(:,i)' * U(:,i));
                g_jj = real(U(:,j)' * U(:,j));
                g_ij = U(:,i)' * U(:,j);
                
                if abs(g_ij) < tolerance * sqrt(g_ii * g_jj)
                    continue;
                end
                converged = false;

                % --- STEP 2: Calculate Rotation (Uses sqrt and invsqrt) ---
                % Complex Jacobi rotation calculation
                tau = (g_jj - g_ii) / (2 * abs(g_ij));
                t = sign(tau) / (abs(tau) + sqrt(1 + tau^2)); % Use hardware sqrt
                c = 1 / sqrt(1 + t^2);                        % Use hardware invsqrt
                s = t * c * (g_ij / abs(g_ij));               % Phase-aligned sine

                % --- STEP 3: Vector Updates (SIMD FMA operations) ---
                % These are unit-stride vector updates:
                % U_i_new = c*U_i - conj(s)*U_j
                % U_j_new = s*U_i + c*U_j
                temp_i = U(:,i);
                U(:,i) = c * temp_i - conj(s) * U(:,j);
                U(:,j) = s * temp_i + c * U(:,j);

                % Update V to track singular vectors
                temp_vi = V(:,i);
                V(:,i) = c * temp_vi - conj(s) * V(:,j);
                V(:,j) = s * temp_vi + c * V(:,j);
            end
        end
        if converged
            num_sweeps = sweep;
            break;
        end
    end

    % --- STEP 4: Post-processing ---
    % Singular values are the norms of the columns of U
    for k = 1:n
        S(k) = norm(U(:,k));
        U(:,k) = U(:,k) / S(k); % Normalize U to be unitary
    end
    S = diag(S);

    % --- STEP 5: Sort by singular value (largest to smallest) ---
    s_vec = diag(S);
    [s_sorted, idx] = sort(s_vec, 'descend');
    S = diag(s_sorted);
    U = U(:, idx);
    V = V(:, idx);
end