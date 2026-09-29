# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#
# Export CIFAR-10 calibration tensors as ifm_*.npy files for the mixed-precision
# skill's --calibration-data, using the same dataset and preprocessing as the
# resnet50_bf16_cifar10 tutorial in amd/Vitis-AI (release/6.3,
# versal_2ve/examples/tutorials/resnet50_bf16_cifar10). Each file is one
# [1, 3, 32, 32] input sample. With --model, the matching FP32 reference logits
# are written next to them as ref_*.npy.

import argparse
from pathlib import Path

import numpy as np
import torch
from torchvision import transforms
from torchvision.datasets import CIFAR10


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="calib")
    ap.add_argument("--num", type=int, default=16, help="number of ifm_*.npy files")
    ap.add_argument(
        "--download",
        action="store_true",
        help="download CIFAR-10 if it is not already in --data-dir",
    )
    ap.add_argument(
        "--model", default=None, help="FP32 ONNX; if given, also write ref_*.npy"
    )
    args = ap.parse_args()

    torch.manual_seed(0)
    np.random.seed(0)
    transform = transforms.Compose(
        [
            transforms.Pad(4),
            transforms.RandomHorizontalFlip(),
            transforms.RandomCrop(32),
            transforms.ToTensor(),
        ]
    )
    dataset = CIFAR10(
        root=args.data_dir, train=True, transform=transform, download=args.download
    )

    sess = iname = None
    if args.model:
        import onnxruntime as ort

        sess = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
        iname = sess.get_inputs()[0].name

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(args.num):
        x = dataset[i][0].numpy()[None].astype(np.float32)  # [1, 3, 32, 32]
        np.save(out / f"ifm_{i}.npy", x)
        if sess is not None:
            y = sess.run(None, {iname: x})[0].astype(np.float32)
            np.save(out / f"ref_{i}.npy", y)
    print(f"wrote {args.num} ifm_*.npy files to {out} (shape 1x3x32x32 each)")


if __name__ == "__main__":
    main()
