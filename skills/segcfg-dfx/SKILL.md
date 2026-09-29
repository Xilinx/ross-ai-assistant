---
name: segcfg-dfx
description: Explain how Segmented Configuration interacts with Dynamic Function eXchange (DFX) on AMD Versal devices. Use this if someone asks about using DFX with Segmented Configuration.
license: MIT
compatibility: Requires Vivado 2026.1+ and an AMD Versal Segmented Configuration design using Dynamic Function eXchange.
metadata:
     version: "1.0.0"
     stage: beta
allowed-tools: Read Bash Write
---

## Segmented Configuration + DFX overview
Support for Segmented Configuration feature on top of DFX design flow will be available from the 2026.1 Vivado release. Both PS/PL partition and reconfigurable partitions are to be supported in the same design. For produced configuration images, each reconfigurable partition will still have one reconfigurable image; the full device configuration image of DFX flow is to be replaced by two Segmented Configuration images, such are boot and pld PDI files. The reconfigurable partitions must be within the PL partition, e.g., the PL partition includes all reconfigurable partitions, logically and physically. The PL partition image is the child of the boot partition image and it is the parent of the reconfigurable partition images. Abstract shell design is treated as pure DFX design, no additional constraint is expected.

## Pre-requisites
1. For basic Segmented Configuration flow creation, load the `segcfg-overview` skill and enable Segmented Configuration before converting the project to a DFX design with the instructions below.

## Instructions
1. NoC logical paths in "IP Integrator (block design) RM container " **can't** be set to initial_boot, since end point connected to NoC AXI interface can't be placed in boot partition. The user  needs to ensure that the reconfigurable partitions are properly defined and placed within the PL partition.

2. After block design creation and validation, project should be converted to DFX. Used the following command to convert the project to DFX:
```tcl
     set_property PR_FLOW 1 [current_project]
```

Set this property **before** enabling reconfigurability on the RM container (do this before steps 3 and 4).

3. Reconfigurable module in block design is expected to be container type, since RM container is required for DFX design. If user creates RM as non-container type, this will cause issue during implementation, since DFX flow expects RM to be container type. 
a. To create RM container, follow the steps below:

- In the top-level block design, create a hierarchy that contains the instances you want inside the reconfigurable region:
- Select the IP blocks → right-click → Create Hierarchy
- Right-click the created hierarchy → Create Block Design Container
- Provide the BDC name → Vivado creates a new child .bd source in the Sources window
(“The BDC name specified … is assigned to the .bd source file … e.g. rp_container.bd”.)

b. To create container instance in top level block design, follow the steps below:
- Once the BDC is created, the hierarchy in the top canvas is replaced by a BDC cell instance that instantiates the new .bd you created.
- You’ll see the BDC cell on the top BD canvas (and you can double-click to open the customize dialog later).

- Convert RM  instance into container. Refer to following example command to convert RM instance into container type:
```tcl
create_bd_design -cell [get_bd_cells /rp_container] rp_container
set new_cell [create_bd_cell -type container -reference rp_container rp_container_temp]
replace_bd_cell [get_bd_cells /rp_container] $new_cell
delete_bd_objs  [get_bd_cells /rp_container]
set_property name rp_container $new_cell
```
- Go to step 4. to make the container reconfigurable.

4. To make RM container reconfigurable the following command should be executed:
```tcl
     set_property CONFIG.ENABLE_DFX {true} [get_bd_cells rp_container]
```
This is required for the tool to recognize the partition as reconfigurable and to generate the appropriate images and constraints for it. The user also needs to ensure that the reconfigurable partitions are properly defined and placed within the PL partition, and that they are connected to the necessary interfaces for their intended functionality.

5. Create pblock constraint that will contain several clock regions from project device, and add RM reconfigurable module cells to it. Example of pblock constraint for RM container:
```tcl
     create_pblock pblock_rp
     add_cells_to_pblock [get_pblocks pblock_rp_container] [get_cells -hierarchical \
     path/to/rp_container]
     resize_pblock [get_pblocks pblock_rp] -add {CLOCKREGION_X2Y2:CLOCKREGION_X3Y3}
```
Add constraint to project.This is required for the tool to recognize the partition as reconfigurable and to generate the appropriate images and constraints for it.

6. The boot image is expected to be identical to the one generated with DFX feature turned off. Exceptions are expected only in rare cases, such as HSR master bank special handling for blackbox design.

7. The pld image is expected to use pure DFX design bitmap and everything else should be identical to the one generated with DFX feature turned off. Comparing to files generated with DFX feature off, no rnpi configuration difference is expected. PLM nodes and design ID related with DFX partitions are exceptions. Comparing to files generated with DFX feature off, rcdo bitmap differences are expected only in RCLK clock buffer, since DFX design instead of flat design bitmap is used. PLM node and design ID handling wise, the pld image should include DFX partitions as children.
