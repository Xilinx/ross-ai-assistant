---
name: segcfg-overview
description: Explain Segmented Configuration concepts for AMD Versal devices — what it is, why it exists, the two-phase boot model, supported device families, and how it compares to standard boot. Use this if someone asks what Segmented Configuration is, how it works, or whether it applies to their device.
license: MIT
compatibility: Applies to AMD Versal and Versal Gen 2 devices and their supported Vivado Segmented Configuration flows.
metadata:
   version: "1.0.0"
   stage: beta
allowed-tools: Read Bash Write
---

Explain the AMD Versal Segmented Configuration feature at a conceptual level and orient the user to the overall flow.

## Instructions

1. Explain **The Need** — why Segmented Configuration exists:
   - Demand for faster boot and flexible PL configuration
   - Boot the system to U-Boot/Linux OS before PL is loaded, optimizing time and flash size
   - Load PL image dynamically from a centralized image repository or boot device
   - Deliver PL updates on the fly without full device reprogramming

2. Explain the **fundamental design goals**:
   - Approach alignment to silicon features and boundaries, not legacy tool structures
   - Collaboration between Versal silicon, Vivado, Vitis, and embedded software groups
   - Build on a standard "flat" design flow — no feature loss, no new project type
   - Minimize restrictions, complexity, and user intervention
   - Ensure compatibility with related solutions (Linux, PCIe Tandem, DFX, fallback, security)

3. Explain **Booting process split into two distinct phases**:
   - **boot.pdi**: Contains PS, HNoC (Hard NoC), and DDRMC only. Boots processors and memory.
   - **pld.pdi**: Contains PL, AIE, VCU, ISP, VNoC, and any remaining resources. Loaded at runtime.
   - PS-DDR NoC paths are automatically flagged for the boot partition
   - Standard flat Vivado implementation flow generates both PDI files from a single design
   - Isolation exists on the PS-PL boundary until pld.pdi is loaded

4. Explain the **Software Stack**:
   - Boot partition brings up: PLM, PSM, RPU (optional), NoC, DDR, ATF, U-Boot, Linux
   - PL partition adds: PL logic, ISP/VCU (Gen 2), ASU ELF, AIE array
   - Each phase is self-contained — the OS is running before PL is ever configured

5. Present **Device Support** as of Vivado 2025.2:

   **Production — Segmented Configuration optional:**
   | Device Family | Example Parts |
   |---------------|---------------|
   | Versal Prime Series | VM1102–VM2902 |
   | Versal AI Core Series | VC1502–VC2802 |
   | Versal AI Edge Series | VE1752–VE2802 |
   | Versal Premium Series | VP1002–VP2802 (SSI) |
   | Versal HBM Series | VH1522–VH1782 (SSI) |
   | Development Kits | VCK190, VEK280, VHK158, VMK180, VPK120, VPK180, Alveo V70/V80 |

   **Production — Segmented Configuration always enabled (cannot be disabled):**
   | Device Family | Parts |
   |---------------|-------|
   | Versal AI Edge Series Gen 2 | 2VE3804, 2VE3858 |
   | Versal Prime Series Gen 2 | 2VM3858 |

   **Early Access:**
   - Additional Versal AI Edge Gen 2, Prime Gen 2 parts (check release notes for current list)

   Note: Versal RF Series is the LAST family where Segmented Configuration is optional.
   All future adaptive SoC devices will have it as the only supported configuration flow.

   
6. Explain **enabling Segmented Configuration**:

   For optional (Gen 1) devices — two methods:
   ```
   GUI: Tools > Settings > General > check "Project is a Segmented Configuration project"
   ```
   ```tcl
   set_property SEGMENTED_CONFIGURATION true [current_project]
   ```

   For mandatory (Gen 2) devices:
   - Property is pre-set and cannot be disabled
   - No Tcl command needed; segmented config is always active

   For non-project mode:
   ```tcl
   link_design -part <part>
   set_property segmented_configuration true [current_design]
   ```

7. Summarize the **Vivado tool flow** at a high level:
   - Design entry: block design (IP Integrator) or RTL (Modular NoC)
   - Set NoC `initial_boot` paths for DDR connectivity in boot partition
   - Run standard synthesis → implementation → `write_device_image`
   - Two PDI files are generated: `boot.pdi` and `pld.pdi`
   - Validate with Segmented Configuration DRCs (SEGCONFIG-1 through SEGCONFIG-7)

8. Point to next steps based on user intent:
   - Starting a new design → follow the project setup and enablement steps above
   - Setting up NoC boot paths → set and verify the NoC `initial_boot` paths in Vivado
   - Understanding design constraints → use `/segcfg-design-considerations`
   - Generating PDI images → run implementation and `write_device_image` as described above
   - PL reload across multiple images → use `/segcfg-pl-reload`
   - DFX + Segmented Configuration → use `/segcfg-dfx`
   - Programming devices → use `/segcfg-hw-manager`
   - Building system software and runtime deployment → use the appropriate system-software workflow
