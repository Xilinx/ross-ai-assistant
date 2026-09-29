# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#
# Accuracy and fidelity of an ONNX model on the CIFAR-10 test set (CPU). The
# VINT8 / VINT8+BF16 models use only standard ONNX QDQ ops, so plain ONNXRuntime
# runs them directly -- no custom-op library needed.
#
# Reports top-1 and top-k accuracy. With --reference, the same images are also
# run through the FP32/BF16 reference model and the logit fidelity against it is
# reported (agreement, cosine similarity, PSNR). Top-1 on the full 10k test set
# still has a ~0.4% standard error, so the fidelity metrics are what separate
# two designs that are statistically tied on accuracy.

import argparse

import numpy as np
import onnxruntime as ort
from torchvision import transforms
from torchvision.datasets import CIFAR10


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    if mse == 0.0:
        return float("inf")
    peak = float(np.max(np.abs(b)))
    return 10.0 * np.log10(peak**2 / mse)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--num", type=int, default=500, help="test images to evaluate")
    ap.add_argument("--topk", type=int, default=2, help="k for the top-k metric")
    ap.add_argument(
        "--download",
        action="store_true",
        help="download CIFAR-10 if it is not already in --data-dir",
    )
    ap.add_argument(
        "--reference",
        default=None,
        help="FP32/BF16 reference ONNX; also report logit fidelity against it",
    )
    args = ap.parse_args()

    ds = CIFAR10(
        root=args.data_dir,
        train=False,
        transform=transforms.ToTensor(),
        download=args.download,
    )
    sess = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    ref_sess = ref_iname = None
    if args.reference:
        ref_sess = ort.InferenceSession(
            args.reference, providers=["CPUExecutionProvider"]
        )
        ref_iname = ref_sess.get_inputs()[0].name

    top1 = topk = agree = 0
    cos_sims: list[float] = []
    psnrs: list[float] = []
    n = min(args.num, len(ds))
    for i in range(n):
        img, label = ds[i]
        x = img.numpy()[None].astype(np.float32)
        logits = sess.run(None, {iname: x})[0].ravel()
        order = np.argsort(logits)[::-1]
        top1 += int(order[0] == label)
        topk += int(label in order[: args.topk])
        if ref_sess is not None:
            ref = ref_sess.run(None, {ref_iname: x})[0].ravel()
            agree += int(order[0] == int(np.argmax(ref)))
            denom = np.linalg.norm(logits) * np.linalg.norm(ref)
            cos_sims.append(float(logits @ ref / denom) if denom else 1.0)
            psnrs.append(psnr(logits, ref))

    print(
        f"{args.model}: top-1 = {100.0 * top1 / n:.2f}% ({top1}/{n}), "
        f"top-{args.topk} = {100.0 * topk / n:.2f}% ({topk}/{n})"
    )
    if ref_sess is not None:
        finite = [p for p in psnrs if np.isfinite(p)]
        print(
            f"  vs {args.reference}: agreement = {100.0 * agree / n:.2f}% "
            f"({agree}/{n}), cos = {np.mean(cos_sims):.6f}, "
            f"min cos = {np.min(cos_sims):.6f}, "
            f"PSNR = {np.mean(finite):.2f} dB, min PSNR = {np.min(finite):.2f} dB"
        )


if __name__ == "__main__":
    main()
