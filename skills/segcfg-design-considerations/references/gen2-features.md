# Gen 2 Device-Specific Features for Segmented Configuration

Applies to: Versal AI Edge Gen 2 (2VExxxx) and Versal Prime Gen 2 (2VMxxxx)

## Key Differences from Gen 1

- Segmented Configuration is **always enabled** — cannot be disabled
- Uses `versal_ps` IP (PS Wizard) instead of `versal_cips`
- Uses `axi_noc2` instead of `axi_noc`
- Software build uses **Yocto** (meta-xilinx-tools), not PetaLinux
- Single PDI concatenation with two `include` statements is supported

## VCU (Video Codec Unit) and ISP (Image Signal Processor)

These hard IP blocks in Gen 2 devices depend on clock resources located in the PL domain.

- VCU and ISP are **bundled with the PL domain** in the pld.pdi image
- They are **unavailable** until pld.pdi is loaded
- Applications requiring VCU or ISP must wait for PL configuration

## ASU Soft Crypto Extension

The Application Security Unit (ASU) supports programmable soft cryptographic algorithms.

- Enable in PS Wizard: check **Enable PL extension** checkbox
- The PL extension is programmed as part of pld.pdi
- Unavailable until pld.pdi is loaded
- Allows adding or updating crypto functions post-deployment

## 10GbE FIFO and TSU (Time Stamp Unit)

The 10GbE Controller in Gen 2 has two PL-dependent features:

### FIFO Interface
- Can be sourced from PL for direct Ethernet packet access
- Runtime selection: GEM register selects between DMA and external FIFO input
- Enable in PS Wizard: **MMI Peripherals** section when 10GbE is enabled
- Unavailable until PL is configured

### Time Stamp Unit (TSU)
- Can be clocked from PL clocking resources
- Runtime selection: `TSU_CLK_LB_SEL` register selects PS or PL source
- Enable in PS Wizard: **MMI Peripherals** section when 10GbE is enabled
- Unavailable until PL is configured

Reference: [AM026 - Versal AI Edge Series Gen 2 and Prime Series Gen 2 TRM](https://docs.amd.com/r/en-US/am026-versal-ai-edge-prime-gen2-trm/10G-Ethernet-MAC)

## DPDC (Display Port Controller)

Certain display modes require PL-sourced clocks.

### Affected Modes
- Live mode
- Mixed mode

### "DP Required Before PL Config" Feature
- Enable in PS Wizard → DPDC Configuration → check **DP required before PL Config**
- With this option, DPDC uses PS-based GPU_CLK initially
- After pld.pdi is loaded, hardware switches to the PL-based clock
- A momentary glitch in video output will occur during the clock switch (DisplayPort retraining)
- Full driver support for this switchover is scheduled for a future release

Reference: [AM026 - Display Controller section](https://docs.amd.com/r/en-US/am026-versal-ai-edge-prime-gen2-trm/Display-Controller)

## Known Issues for Gen 2 Devices (2025.2)

### Master Bank Restriction
- If the boot partition uses any DDRMC X5IO banks, the master bank (bank 700, leftmost along bottom) is recommended to be one of them
- Unsupported: connecting PS to the right-most DDRMC when the left-most DDRMC (and master X5IO bank) is connected to the PL domain
- Fix planned for a future Vivado release

### DDRMC5 PL Reload Restrictions
- If any DDRMC5 site has both ports connected to PL domains, the controller can lose connection to PS after a PL Reload event
- See [AR39028](https://adaptivesupport.amd.com/s/article/000039208) for supported and unsupported DDRMC5 configurations

### DRC: SEGCONFIG-5
- Master bank X5IO port used in secondary PLD partition but not in boot partition
- Resolution: Ensure the master bank is either in the boot partition or completely unused
