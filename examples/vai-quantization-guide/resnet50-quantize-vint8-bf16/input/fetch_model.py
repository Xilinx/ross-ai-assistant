# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#
# Fetch the CIFAR-10 ResNet50 weights from the Vitis-AI
# `resnet50_bf16_cifar10` tutorial and export them to FP32 ONNX. This ONNX is
# the source for both the BF16 reference design and the quantized designs.

import argparse
import urllib.error
import urllib.request
from pathlib import Path

import torch
from torchvision.models import resnet50

REPO = "amd/Vitis-AI"
REF = "release/6.3"
TUTORIAL = "versal_2ve/examples/tutorials/resnet50_bf16_cifar10"
WEIGHTS_PATH = f"{TUTORIAL}/models/resnet_trained_for_cifar10.pt"
WEIGHTS_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/{WEIGHTS_PATH}"


def build_model() -> torch.nn.Module:
    """ResNet-50 backbone with the 10-class CIFAR-10 head (tutorial definition)."""
    model = resnet50(weights=None)
    model.fc = torch.nn.Sequential(
        torch.nn.Linear(2048, 64),
        torch.nn.ReLU(inplace=True),
        torch.nn.Linear(64, 10),
    )
    return model


def download_weights(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {WEIGHTS_URL}")
    try:
        with urllib.request.urlopen(WEIGHTS_URL) as response, dest.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"download failed ({exc.code} {exc.reason}): {WEIGHTS_URL}\n"
            "If you are behind a proxy, set https_proxy, or pass --weights <path> "
            "pointing at a local clone of\n"
            f"  {REPO}:{REF}/{WEIGHTS_PATH}"
        )
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("models"))
    ap.add_argument(
        "--weights",
        type=Path,
        default=None,
        help="local resnet_trained_for_cifar10.pt (skips the download)",
    )
    args = ap.parse_args()

    weights = args.weights or download_weights(
        args.out_dir / "resnet_trained_for_cifar10.pt"
    )

    model = build_model()
    # weights_only=True: the checkpoint is a plain state_dict, no pickled code.
    model.load_state_dict(
        torch.load(str(weights), map_location="cpu", weights_only=True)
    )
    model.eval()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "resnet_trained_for_cifar10.onnx"
    torch.onnx.export(
        model,
        torch.randn(1, 3, 32, 32),
        str(out),
        export_params=True,
        opset_version=17,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}},
    )
    print("saved", out)


if __name__ == "__main__":
    main()
