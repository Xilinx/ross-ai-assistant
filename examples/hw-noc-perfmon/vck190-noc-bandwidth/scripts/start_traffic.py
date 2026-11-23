#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Start or stop DDR traffic from the NoC traffic generator in the VCK190
ChipScoPy example design, so the hw-noc-perfmon skill has traffic to measure.

The generator (chipscopy_i/noc_tg_bc/noc_tg, a Performance AXI Traffic
Generator) comes up held in reset with an empty instruction memory, and
chipscope-mcp has no tool to load it. To start traffic, this script:

  1. programs the device with --pdi;
  2. releases the generator's resets through the design's VIO;
  3. loads one instruction that streams 4 KB bursts over the first 2 GB of
     DDR and loops forever, then starts it;
  4. checks the generator is running, and exits.

The traffic keeps running after the script exits, until the device is
reprogrammed or the script is run with --stop.

Starting always reprograms the device. After the generator has been stopped
while writing, later write traffic never starts, with no done or error flag,
until the device is programmed again; the likely cause is a write burst cut off
halfway. Reads are not affected.

It needs the chipscopy Python package (installed with chipscope-mcp) and a
running cs_server (in Vivado's bin directory).

Examples:
  python start_traffic.py --hw-server TCP:myhost:3121 \\
      --pdi chipscopy_wrapper.pdi --ltx chipscopy_wrapper.ltx
  python start_traffic.py --hw-server TCP:myhost:3121 \\
      --pdi chipscopy_wrapper.pdi --ltx chipscopy_wrapper.ltx --pattern write
  python start_traffic.py --hw-server TCP:myhost:3121 --ltx chipscopy_wrapper.ltx --stop
"""

import argparse
import sys
import time

from chipscopy import create_session, delete_session
from chipscopy.api.patg import (
    AddressPattern,
    PATGInstruction,
    PATGTrigger,
    PATGTriggerInterfaceMode,
    TransactionType,
)

TG_HIER = "chipscopy_i/noc_tg_bc"
TG_VIO = f"{TG_HIER}/axis_vio_0"
# Debug-hub address of the generator's trigger block (axi_dbg_hub_0 in the CED).
TRIGGER_BASE_ADDRESS = 0x201_8000_0000
# The generator's view of DDR: C0_DDR_LOW0, 2 GB at address 0.
DDR_BASE = 0x0
DDR_HIGH = 0x7FFF_FFFF


def parse_args():
    p = argparse.ArgumentParser(
        description="Start or stop DDR traffic from the NoC traffic generator in the VCK190 ChipScoPy example design.")
    p.add_argument("--hw-server", required=True, help="hw_server URL, e.g. TCP:myhost:3121")
    p.add_argument("--cs-server", default="TCP:localhost:3042", help="cs_server URL (default %(default)s)")
    p.add_argument("--ltx", required=True, help="probes file (chipscopy_wrapper.ltx)")
    p.add_argument("--pdi", help="PDI to program before starting (chipscopy_wrapper.pdi); required unless --stop")
    p.add_argument("--pattern", choices=["read", "write"], default="read",
                   help="traffic direction (default %(default)s)")
    p.add_argument("--stop", action="store_true", help="stop the traffic instead of starting it")
    args = p.parse_args()
    if not args.stop and not args.pdi:
        p.error("--pdi is required to start traffic: each start reprograms the device")
    return args


def make_instruction(pattern):
    """One instruction: 64-beat x 64-byte INCR bursts over DDR_BASE..DDR_HIGH,
    repeated forever (infinite_loop)."""
    return PATGInstruction(
        transaction_type=TransactionType.READ if pattern == "read" else TransactionType.WRITE,
        axi_len=0x3F,
        axi_size=6,
        axi_burst=1,
        base_addr=DDR_BASE,
        high_addr=DDR_HIGH,
        address_pattern=AddressPattern.INCREMENT_BY_VALUE,
        addr_incr_by="AUTO_INCR",
        data_integrity=0,
        loop=1,
        loop_start=1,
        loop_count=0x86A0,
        infinite_loop=1,
    )


def tg_status(vio):
    probes = vio.read_probes()
    return {name.split("/")[-1]: probes[name]["value"] for name in probes}


def main():
    args = parse_args()
    session = create_session(cs_server_url=args.cs_server, hw_server_url=args.hw_server)
    try:
        device = session.devices.filter_by(family="versal").get()
        if not args.stop:
            print(f"Programming {args.pdi}")
            device.reset()
            device.program(args.pdi)
        device.discover_and_setup_cores(ltx_file=args.ltx)

        try:
            vio = device.vio_cores.get(name=TG_VIO)
        except ValueError:
            sys.exit(f"No VIO {TG_VIO}: is the device programmed with the VCK190 ChipScoPy example design?")

        if args.stop:
            trigger = PATGTrigger(TRIGGER_BASE_ADDRESS, device, None,
                                  interface_mode=PATGTriggerInterfaceMode.DIM, vio=vio)
            for tg in trigger.tgs:
                tg.stop()
            print(f"Traffic stopped. Generator status: {tg_status(vio)}")
            return

        # Release the trigger block and the generator from reset.
        vio.write_probes({f"{TG_HIER}/noc_sim_trig_rst_n": 1, f"{TG_HIER}/noc_tg_tg_rst_n": 1})

        trigger = PATGTrigger(TRIGGER_BASE_ADDRESS, device, {0: [make_instruction(args.pattern)]},
                              interface_mode=PATGTriggerInterfaceMode.DIM, vio=vio)
        for tg in trigger.tgs:
            tg.program()
            tg.start()

        time.sleep(1)
        status = tg_status(vio)
        if status.get("noc_tg_axi_tg_error"):
            sys.exit(f"The traffic generator reports an error: {status}")
        if status.get("noc_tg_axi_tg_done"):
            sys.exit(f"The traffic generator finished instead of looping: {status}")
        print(f"Traffic running: continuous DDR {args.pattern}s through the NoC "
              f"(generator status {status}). It keeps running after this script exits.")
    finally:
        delete_session(session)


if __name__ == "__main__":
    main()
