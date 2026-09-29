# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# Pin the dynamic batch dimension of an ONNX model's input/output to 1 (VAIML
# requires fully-static shapes).
import sys

import onnx

inp, outp = sys.argv[1], sys.argv[2]
m = onnx.load(inp)
for t in list(m.graph.input) + list(m.graph.output):
    d = t.type.tensor_type.shape.dim
    if len(d) and (d[0].dim_param or d[0].dim_value == 0):
        d[0].ClearField("dim_param")
        d[0].dim_value = 1
onnx.save(m, outp)
print("pinned batch=1 ->", outp)
