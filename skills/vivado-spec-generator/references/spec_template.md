<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# DesignSpec functional-contract template

The spec describes required behavior, not a block-design implementation.

```json
{
  "name": "design_name",
  "description": "System intent",
  "target_device": "exact-part",
  "board_part": "vendor:board:part0:version",
  "blocks": [
    {
      "name": "functional_block",
      "description": "Observable function",
      "workload_type": "sensor_interface",
      "interfaces": [
        {
          "name": "video_out",
          "protocol": "AXI4-Stream",
          "width_bits": 48,
          "clock_mhz": 200,
          "direction": "out",
          "scope": "internal"
        }
      ],
      "constraints": {},
      "parameters": {}
    }
  ],
  "functional_contracts": {
    "connections": [
      {
        "source": "source_block.output",
        "destination": "sink_block.input",
        "protocol": "AXI4-Stream",
        "purpose": "Required data path",
        "required": true
      },
      {
        "source": "boot_master.config",
        "destination": "configured_block.control",
        "protocol": "AXI4 via NoC",
        "purpose": "A route whose traffic the sources state",
        "read_bandwidth_mb_s": 5,
        "write_bandwidth_mb_s": 5,
        "read_burst_length": 4,
        "write_burst_length": 4,
        "initial_boot": true,
        "required": true
      }
    ],
    "control_reachability": [
      {
        "master": "linux_apu",
        "target": "controlled_block",
        "access": "read_write",
        "required": true
      }
    ],
    "interrupts": [
      {
        "source": "controlled_block",
        "outputs": ["irq"],
        "destination": "processing_system.fabric_irq",
        "allow_aggregation": true,
        "required": true
      }
    ],
    "memory_reachability": [
      {
        "master": "data_mover",
        "memory": "system_memory",
        "access": "write",
        "minimum_bandwidth_mb_s": 1000,
        "traffic_class": "ISOCHRONOUS",
        "required": true
      }
    ],
    "fixed_address_windows": [
      {
        "name": "driver_window",
        "target": "mailbox.memory",
        "block": "mailbox",
        "masters": ["linux_apu"],
        "offset": "0x80000000",
        "range": "0x2000",
        "reason": "Hard-coded driver constant"
      }
    ],
    "visibility_policy": [
      {
        "action": "deny",
        "masters": ["untrusted_master"],
        "targets": ["control_plane"],
        "reason": "No functional access requirement"
      }
    ],
    "clock_reset": [
      {
        "target": "functional_block",
        "clock_mhz": 200,
        "reset_polarity": "interface_defined",
        "synchronized": true,
        "required": true
      }
    ],
    "software": [
      {
        "os": "linux",
        "visible_devices": ["controlled_block", "mailbox"],
        "device_tree_required": true,
        "requirements": ["valid MMIO range", "valid interrupt route"]
      }
    ]
  },
  "clock_domains": [],
  "global_constraints": {},
  "power_budget_watts": null
}
```

## Field rules

- `board_part`: exact board VLNV when the request identifies a board.
- `blocks`: application and platform functions only. Do not enumerate generated
  fabric merely to resemble another implementation.
- Preserve semantic functions as separate blocks even when one catalog IP implements
  several of them. For example, keep `processing_system` and `pcie_endpoint` as distinct
  functional blocks when both are required; technology binding may map both to PS wizard.
- A block covers one kind of function. Do not merge unrelated peripheral kinds into a
  single convenience block because they share a connector or a pin group. Separately
  instantiable units get one block each, not one block with a `*_count` parameter; the
  internal tiles, channels or lanes of a single unit stay parameters of its one block.
- Endpoint references use `<block>.<interface>` where a concrete interface exists.
  Semantic roles such as `linux_apu` and `system_memory` are allowed for hard
  processing-system resources.
- Interface `scope` is `internal`, `top_level`, or `external`. Use `top_level`
  when IPI must create an address-bearing design boundary; do not let a top-level
  AXI ingress be mistaken for a slave segment inside the processing system.
- `connections`: point-to-point functional paths. They are independent of
  intervening SmartConnect, NoC, adapters, or hierarchy. When the sources
  distinguish a required path from a similar-looking generic one (a
  *dedicated*/*bridge* route instead of a generic fabric stream, for example),
  name that distinguishing word in `protocol` itself, not only in `purpose`.
- `control_reachability`: software-visible control obligations.
- `interrupts.outputs`: every required output from the source function. The
  downstream implementation may aggregate them when `allow_aggregation` is true.
- `memory_reachability`: required memory access and bandwidth. Omit a bandwidth
  only when the prompt provides no defensible rate.
- `fixed_address_windows`: only offsets fixed by software, architecture, boot ROM,
  or an external protocol. Automatically assign all other offsets. When a supplied
  address map already labels a master or a target, quote that label as written on
  both sides of the window rather than renaming it; the labels are part of the
  contract. Use your own `<block>.<interface>` reference only for a window the
  supplied map does not name. `block` names the declared block that owns the window;
  every window for one map target names the same block, and targets that share a
  prefix before the first `.` bind to one block or to one block each.
- `visibility_policy`: functional allow/deny decisions, not an address table.
- Every target group needs defined members, not merely a label such as `control_plane`.
  Use explicit targets or `global_constraints.reference_groups`, an object mapping
  group names to nonempty lists of declared targets or other acyclic groups.
  Declare external actors in `global_constraints.external_actors` (a string list).
  Block/interface names, named memory regions and fixed-address-window names can
  serve as targets; physical implementation hierarchy is not required.
- Use `processing_system.pmc`, `processing_system.ppu`, `processing_system.asu`, and
  `processing_system.mmi_peripheral_masters` for those standard PS visibility actors.
- `clock_reset`: behavior. It must not prescribe clock/reset IP counts. Preserve
  a stated frequency exactly as given; do not round it or apply a tolerance the
  sources did not state. When the sources single out one clock among several on
  the same block (its *core* clock, as opposed to a bus or reference clock),
  say so in `target` — the bare block name, or a qualifier containing "core" —
  rather than leaving the distinction only in prose.
- `software`: devices and properties that BSP/device-tree generation must expose.
  Preserve every supplied software ABI field/bit, encoding, ownership and ordering
  requirement in structured parameters or unambiguous requirement text. Generic
  compatibility statements and references to source documents do not substitute
  for the required detail in the JSON.

Canonical camera-receiver parameter names are `lanes`, `line_rate_mbps`,
`pixel_format`, `pixels_per_clock`, `virtual_channels`, `csi_buffer_depth`,
`active_lane_filter`, `user_data_type_filter`, `dphy_register_interface`, and `isp_bridge`. The validator rejects
near-synonyms so downstream consumers do not silently miss an operating-point requirement.
The same applies to a parameter's *value*, not only its name: when the sources
state an acceptance policy in plain language ("accept every virtual channel",
"no ID filtering"), give `virtual_channels` the sources' own policy word as its
value rather than a paraphrase of the same behavior — a description that reads
correctly to a person but does not match the stated policy word is still an
unmet requirement to anything that compares the value.

## Workload types

`control_logic`, `os_application`, `bare_metal_firmware`, `stream_dsp`,
`vector_compute`, `ml_inference`, `packet_processing`, `video_pipeline`,
`sensor_interface`, `memory_controller`, `crypto_accelerator`, `motor_control`,
`protocol_bridge`, `compression`, `signal_chain`, `custom_pl`, `custom_aie`,
`custom_ps`.
