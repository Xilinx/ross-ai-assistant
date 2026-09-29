#!/usr/bin/env python3
# Copyright (C) 2022 - 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Compile ONNX models for VitisAI or run CPU inference.

Examples:
    # Compile for VitisAI (cache dir = model stem, removes old cache)
    python compile.py model_a.onnx --vitisai-config vitisai_config.json

    # Compile keeping existing cache
    python compile.py model_a.onnx --keep-cache

    # Run CPU inference (like run_cpu_reference.py)
    python compile.py model_a.onnx --cpu --input-dir input_data --output-dir output_a

    # Compile with specific cache directory
    python compile.py model_b.onnx --cache-dir custom_cache
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).parent))
import custom_ops_utils  # noqa: E402

if importlib.util.find_spec("flexml") is None:
    print(
        "TA environment not found, please source the TA environment before running this script"
    )
    sys.exit(1)


def is_no_such_device_error(e: Exception) -> bool:
    return "ERROR during FlexMLHWRunner creation:  No such device" in str(e)


def _set_unlimited_stack() -> None:
    """Raise the stack limit as far as the OS allows.

    `resource` is POSIX-only, so this is a no-op on Windows rather than an
    ImportError.
    """
    if sys.platform == "win32":
        return

    import resource

    try:
        resource.setrlimit(
            resource.RLIMIT_STACK,
            (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        )
    except OSError:
        _, hard = resource.getrlimit(resource.RLIMIT_STACK)
        resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))


def scan_aiecompiler_errors(cache_dir: Path) -> list[str]:
    """Return the aiecompiler errors found in the compile logs (empty if none).

    An L2 buffer overlap promoted to an error via
    `--msg-severity="77-23879:error:100"`, for instance, produces no hardware
    artifacts, so the reported ERROR is the only evidence.
    """
    import re

    err_re = re.compile(r"^ERROR:\s*\[")  # e.g. "ERROR: [aiecompiler 77-23879] ..."
    summary_re = re.compile(
        r"ERROR:(\d+)\)"
    )  # e.g. "(WARNING:42, CRITICAL-WARNING:1, ERROR:1)"
    errors: list[str] = []
    for name in ("AIECompiler.log", "aiecompiler-flexml.log"):
        for log in sorted(cache_dir.rglob(name)):
            try:
                lines = log.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                s = line.strip()
                if err_re.match(s):
                    errors.append(f"[aiecompiler] {log}: {s}")
                    continue
                m = summary_re.search(s)
                if m and int(m.group(1)) > 0:
                    errors.append(f"[aiecompiler] {log}: {s}")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def scan_errors(cache_dir: Path) -> list[str]:
    """Collect the errors reported by each compile component, tagged per component.

    A VitisAI compile can "succeed" at the top level (no exception, prints
    "Compilation successful", exit 0) while a component underneath FAILED: VAIP
    catches the failure and silently falls the subgraph back to CPU. So every
    component's logs are scanned explicitly and the findings are surfaced.

    Only aiecompiler is scanned today. Add the tensor_expr / frontend / backend
    scanners here as they are written; each returns messages already tagged with
    its component, so the caller only has to print them.
    """
    errors: list[str] = []
    errors += scan_aiecompiler_errors(cache_dir)
    return errors


def run_codegen_analysis(cache_dir: Path) -> None:
    """Post-compile codegen health check on the AIE disassembly (.lst).

    Off by default; when enabled it runs after a successful compile. Any
    ERROR-severity finding (e.g. a scalar software float-divide `__divsf3`,
    which should be `aie::inv`) fails the build -- a non-fatal warning would
    just be ignored in an automated loop, so we only error on things we are
    sure about, and each finding carries a machine-readable code and the
    canonical fix.
    """
    import post_compile_checks

    errors = post_compile_checks.analyze(cache_dir)
    if errors:
        codes = ", ".join(sorted({f.code for f in errors}))
        raise RuntimeError(
            f"codegen analysis reported error(s) [{codes}]; see findings above "
            f"and fix them (or run post_compile_checks.py {cache_dir} for details)"
        )


def compile_vitisai(
    model_path: Path,
    vitisai_config: Path,
    cache_dir: Path | None = None,
    keep_cache: bool = False,
    analyze: bool = False,
) -> None:
    """Compile model for VitisAI.

    Args:
        model_path: Path to ONNX model
        vitisai_config: Path to vitisai_config.json
        cache_dir: Cache directory (default: model stem)
        keep_cache: If False (default), remove cache directory before compilation
        analyze: If True, run the post-compile codegen analysis (off by default)
    """
    if cache_dir is None:
        cache_dir = Path(model_path.stem)

    # Remove cache directory by default for clean compilation
    if not keep_cache and cache_dir.exists():
        import shutil

        print(f"Removing existing cache directory: {cache_dir}/")
        shutil.rmtree(cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Compiling {model_path.name} for VitisAI...")
    print(f"  Cache directory: {cache_dir}/")
    print(f"  VitisAI config: {vitisai_config}")

    # Register custom ops if config has them
    if vitisai_config.exists():
        custom_ops_utils.register_custom_ops_from_vitisai_config(vitisai_config)

    options = ort.SessionOptions()

    # Unlimited stack is required for larger designs.
    _set_unlimited_stack()
    try:
        ort.InferenceSession(
            str(model_path),
            options,
            providers=["VitisAIExecutionProvider"],
            provider_options=[
                {
                    "target": "VAIML",
                    "config_file": str(vitisai_config),
                    "ai_analyzer_visualization": 1,
                    "ai_analyzer_profiling": 1,
                    "cache_dir": str(cache_dir),
                    "cache_key": "cache",
                }
            ],
        )
        success_msg = f"✓ Compilation successful: {model_path.name}"
    except Exception as e:
        if is_no_such_device_error(e):
            success_msg = (
                f"✓ Compilation successful (no device for execution): {model_path.name}"
            )
        else:
            raise

    # A component can fail while the top level still reports success, so never
    # trust the absence of an exception (see scan_errors).
    errors = scan_errors(cache_dir)
    if errors:
        print(
            f"✗ Compilation FAILED: found the following {len(errors)} error(s) "
            f"(subgraph likely fell back to CPU): {model_path.name}",
            file=sys.stderr,
        )
        for line in errors:
            print(f"    {line}", file=sys.stderr)
        raise RuntimeError(f"compile errors reported; see logs under {cache_dir}/")

    print(success_msg)

    # Default-on codegen health check of the AIE disassembly (.lst).
    if analyze:
        run_codegen_analysis(cache_dir)


def run_cpu_inference(
    model_path: Path,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    vitisai_config: Path | None = None,
) -> dict[str, np.ndarray]:
    """Run model on CPU and save outputs.

    Args:
        model_path: Path to ONNX model
        input_dir: Directory with inputs (or None to generate)
        output_dir: Directory to save outputs (default: <model_stem>_outputs)
        vitisai_config: Path to vitisai_config.json for custom ops

    Returns:
        Dict mapping output names to numpy arrays
    """
    if output_dir is None:
        output_dir = Path(f"{model_path.stem}_outputs")

    print(f"Running {model_path.name} on CPU...")

    # Register custom ops if config provided
    if vitisai_config and vitisai_config.exists():
        custom_ops_utils.register_custom_ops_from_vitisai_config(vitisai_config)

    # Load model
    model = onnx.load(model_path)

    # Load or generate inputs
    if input_dir and input_dir.exists():
        feeds = custom_ops_utils.load_inputs(input_dir, model.graph)
    else:
        feeds = custom_ops_utils.generate_and_save_inputs(
            model.graph, input_dir, seed=42, model_stem=model_path.stem
        )

    # Run inference
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    output_names = [o.name for o in session.get_outputs()]
    results = session.run(None, feeds)
    outputs = dict(zip(output_names, results))

    # Print output info
    for name, arr in outputs.items():
        print(
            f"  Output '{name}': shape={arr.shape}, dtype={arr.dtype}, "
            f"min={arr.min():.6f}, max={arr.max():.6f}"
        )

    # Save outputs
    custom_ops_utils.save_outputs(outputs, output_dir)

    print(f"✓ CPU inference successful: {model_path.name}")
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile ONNX models for VitisAI or run CPU inference."
    )
    parser.add_argument("model", type=Path, help="Path to ONNX model")
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Run CPU inference instead of VitisAI compilation",
    )
    parser.add_argument(
        "--vitisai-config",
        type=Path,
        default="vitisai_config.json",
        help="Path to vitisai_config.json (default: vitisai_config.json)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Cache directory for VitisAI (default: model stem)",
    )
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="Keep existing cache directory (default: remove and recompile clean)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Input directory for CPU inference (default: generate random)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for CPU inference (default: <model_stem>_outputs)",
    )

    args = parser.parse_args()

    if not args.model.exists():
        print(f"Error: Model not found: {args.model}", file=sys.stderr)
        return 1

    try:
        if args.cpu:
            run_cpu_inference(
                model_path=args.model,
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                vitisai_config=args.vitisai_config,
            )
        else:
            compile_vitisai(
                model_path=args.model,
                vitisai_config=args.vitisai_config,
                cache_dir=args.cache_dir,
                keep_cache=args.keep_cache,
                analyze=False,
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
