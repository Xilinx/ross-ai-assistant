# Vitis AI AI-Assisted Workflows Reference

This section describes each AI-assisted workflow that ships with Vitis AI.

The workflows are optional. Each task is fully documented for manual execution,
and the manual procedure is linked from every workflow page. If you do not use
an AI coding assistant, follow those links instead. To set the workflows up, see
[the skill installation guide](../../README.md#quick-start).

## Choosing a workflow

| If you want to | Workflow | Manual procedure |
| --- | --- | --- |
| Implement a custom operator that can extend what the NPU can run and/or optimize how it runs. | [Custom operator workflow](vai-custom-op.md) | [Custom operators](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/additional_information/custom_ops/custom_ops_introduction.html) |
| Quantize a model, including per-layer mixed precision | [Model quantization workflow](vai-quantization-guide.md) | [Model Quantization](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_quantization/model_quantization.html) , [Mixed Precision Compilation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/mixed_precision_compilation.html) |
| Increase NPU offload, reduce inference time, or get a model to compile | [Compiler options workflow](vai-flag-configuration.md) | [Vitis AI EP Options](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-options.html) |

### Board access

The workflows steer themselves using measurements taken on hardware. Without
board access they fall back on simulation and host-side compile results, which
are not a one-to-one match for the hardware. They still run, but expect numeric
mismatches, untuned performance, and weaker convergence. Each workflow page
describes what it can still do without a board.

The workflows reach the board over SSH, using either a password or a key. Set
the following before launching your assistant:

```shell
export BOARDHOST=172.16.0.1        # your board's IP address
export BOARD_USER=amd-edf

# Authenticate with a password
export BOARD_PASSWORD=<password>

# Or with an SSH key, instead of a password
# export BOARD_KEY=/mnt/user/ssh_key_file
```

> **Caution**
>
> `BOARD_PASSWORD` is a credential. Keep it out of scripts and version
> control, and prefer a permission-restricted file over a plain shell export.
> See the guidance in [the Vitis AI documentation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/llm-setup.html#configure-llm-access).

To persist these across container restarts, append them to `$HOME/.bashrc`.
If you followed the Vitis AI documentation example
[Claude Code in the Vitis AI Docker container](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/llm-setup.html#claude-code-docker),
this file is mapped to `$WORKDIR/homedir/.bashrc` on the host.

If you prefer not to set them, supply the board details in the prompt instead:

```text
Run the model ./model.onnx on my board using configuration
./vitisai_config.json. The board's IP address is 172.16.0.1, the user is
amd-edf, the password is mypassword.
```

Boot the board and confirm it is reachable over SSH before you start a session.
See the OSPI and SD card boot flow documentation
[OSPI and SD Card Boot Flow](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/setup_and_installation/board_setup_ospi_sd_eou.html)
for board preparation and bootup.

- [Custom operator workflow](vai-custom-op.md)
- [Model quantization workflow](vai-quantization-guide.md)
- [Compiler options workflow](vai-flag-configuration.md)
