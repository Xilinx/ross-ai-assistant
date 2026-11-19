# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Filters and declarations for the vitis-platform skill (SKILL.md steps 4 and
# 5). They apply the known limitations of the ::vitis::pfm discovery commands
# so the selection does not depend on retyping the rules. Source with the
# block design open; the end of this file loads the bundled autopfm.tcl when
# Vivado does not define the ::vitis::pfm commands. The filters work on the
# original candidate dictionaries and return them unchanged, so the results
# can be passed straight to ::vitis::pfm::set_*. apply_defaults runs
# discovery, the filters and every declaration in one call.
#
# Sources cited below:
#   UG1701  Embedded Design Development Using Vitis User Guide (2026.1),
#           "Adding Hardware Interfaces"
#           https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Adding-Hardware-Interfaces
#   XD101   Vitis Tutorials: Platform Creation (2026.1)
#           "Enable AXI Interfaces for the Platform"
#           https://docs.amd.com/r/en-US/Vitis-Tutorials-Platform-Creation-XD101/Enable-AXI-Interfaces-for-the-Platform
#           "Add Interrupt Support"
#           https://docs.amd.com/r/en-US/Vitis-Tutorials-Platform-Creation-XD101/Add-Interrupt-Support
#   MPSoC_Ext_Platform, Versal_Extensible_Embedded_Platform and
#   versal_dfx_platform: AMD example designs in the Vivado 2026.1 CED store
#   (data/xhub/ced/XilinxCEDStore/ced/Xilinx/IPI/*/run.tcl, pfm_decls.tcl).
#
# Not covered: DFX platforms. UG1701 "DFX-Based Hardware Platform" exports
# memory from an AXI NoC inside the Vitis region block design container, which
# reaches DDR in the static region over INI. Opened on its own, that block
# design has no PS and no memory controller, and ::vitis::pfm returns no memory
# or control candidates for it (checked on versal_dfx_platform 1.0, 2026.1).

namespace eval ::vitis_platform {

  # An external port is a platform terminal only when it has an address
  # window: an APERTURES value (block design containers and DFX regions), or
  # an assigned address segment. A slave port (an off-chip master) needs a
  # segment in its own address space; a master port needs one of its slave
  # segments mapped into some address space. Excluded and unassigned
  # segments are not returned by get_bd_addr_segs -of_objects, so they do
  # not count (checked on 2026.1).
  proc port_has_aperture {port} {
    if {[get_property -quiet APERTURES $port] ne ""} { return 1 }
    if {[get_property MODE $port] eq "Slave"} {
      return [expr {[llength [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces -quiet -of_objects $port]]] > 0}]
    }
    foreach seg [get_bd_addr_segs -quiet -of_objects $port] {
      if {[llength [get_bd_addr_segs -quiet -of_objects $seg]]} { return 1 }
    }
    return 0
  }

  # True when a candidate reaches at least one terminal: a processor or
  # memory resource that is not an external port, or an external port with
  # an aperture.
  proc reaches_terminal {resources} {
    foreach r $resources {
      set port [get_bd_intf_ports -quiet $r]
      if {$port eq "" || [port_has_aperture $port]} { return 1 }
    }
    return 0
  }

  # External AXI or INI ports reached from intf through interconnect
  # bridges. ::vitis::pfm::utils::bridge_scan accepts only a peer of the
  # opposite MODE, but a block-design port has the same MODE as the pin it
  # connects to, so ::vitis::pfm never reaches an external port (2026.1 and
  # the 2026.2 sdsoc_pfm.tcl alike).
  proc port_scan {intf} {
    set invert [dict create Master Slave Slave Master]
    set mode [get_property MODE $intf]
    if {![dict exists $invert $mode]} { return {} }
    set ports {}
    foreach peer [find_bd_objs -quiet -thru_hier -relation connected_to $intf] {
      set port [get_bd_intf_ports -quiet $peer]
      if {$port ne ""} {
        if {[get_property MODE $port] eq $mode && [string match {*:interface:[ai][xn]imm_rtl:*} [get_property VLNV $port]]} {
          lappend ports $port
        }
        continue
      }
      set pin [get_bd_intf_pins -quiet $peer]
      if {$pin eq "" || [get_property MODE $pin] ne [dict get $invert $mode]} continue
      set cell [get_bd_cells -quiet -of_objects $pin]
      foreach b [split [get_property -quiet BRIDGES $pin] :] {
        if {$b ne ""} { lappend ports {*}[port_scan [get_bd_intf_pins $cell/$b]] }
      }
    }
    return [lsort -unique $ports]
  }

  # Add to ::vitis::pfm candidates the NoC and SmartConnect cells whose
  # <side> interfaces reach an external port with an aperture, merging the
  # ports into <key> of a cell that is already a candidate.
  proc add_port_terminals {candidates side key} {
    set by_cell [dict create]
    foreach c $candidates { dict set by_cell [dict get $c cell] $c }
    set cells [get_bd_cells -quiet -hierarchical -filter {VLNV=~*:ip:smartconnect:* || VLNV=~*:ip:axi_noc:* || VLNV=~*:ip:axi_noc2:* || VLNV=~*:ip:axi_noc3:*}]
    foreach cell $cells {
      set ports {}
      foreach intf [get_bd_intf_pins -quiet -of_objects $cell -filter "MODE==$side"] {
        foreach p [port_scan $intf] {
          if {[port_has_aperture $p]} { lappend ports $p }
        }
      }
      if {![llength $ports]} continue
      if {[dict exists $by_cell $cell]} {
        set c [dict get $by_cell $cell]
        dict set c $key [lsort -unique [concat [dict get $c $key] $ports]]
      } else {
        set c [dict create cell $cell kind [lindex [split [get_property VLNV $cell] :] 2] $key [lsort -unique $ports]]
      }
      dict set by_cell $cell $c
    }
    return [dict values $by_cell]
  }

  proc ps_cells {} {
    set cells [get_bd_cells -quiet -hierarchical]
    if {![llength $cells]} { return {} }
    set ps [lmap c $cells v [get_property VLNV $cells] {
      expr {[regexp {:ip:(versal_cips|ps_wizard|psx_wizard):} $v] ? $c : [continue]}
    }]
    if {![llength $ps]} { return {} }
    get_bd_cells $ps
  }

  proc is_noc {cell} {
    string match {*:ip:axi_noc*} [get_property VLNV [get_bd_cells $cell]]
  }

  # True when a processor drives one of the cell's AXI slave interfaces
  # directly (INI links between NoCs do not count).
  proc processor_drives {cell} {
    set ps [ps_cells]
    if {![llength $ps]} { return 0 }
    foreach intf [get_bd_intf_pins -quiet -of_objects [get_bd_cells $cell] -filter {MODE==Slave && VLNV=~*:interface:aximm_rtl:*}] {
      foreach peer [find_bd_objs -quiet -thru_hier -relation connected_to $intf] {
        set owner [get_bd_cells -quiet -of_objects $peer]
        if {$owner ne "" && $owner in $ps} { return 1 }
      }
    }
    return 0
  }

  # Control candidates: cells whose slave side traces back to a processor
  # AXI master or to an external slave port with an aperture. When a
  # SmartConnect (or any other non-NoC interconnect) qualifies, the NoCs
  # are dropped: AMD's versal_comn_platform declares control only on
  # /ctrl_smc, "as v++ link will automatically cascade additional as needed"
  # (vek385_pfm_decls.tcl, vck_vek_pfm_decls.tcl). On a Versal PS every NoC
  # the PS reaches would otherwise qualify (five of them on the VEK385).
  proc control_candidates {candidates} {
    set keep {}
    foreach c [add_port_terminals $candidates Slave resource] {
      if {[reaches_terminal [dict get $c resource]]} { lappend keep $c }
    }
    set non_noc [lmap c $keep {expr {[is_noc [dict get $c cell]] ? [continue] : $c}}]
    return [expr {[llength $non_noc] ? $non_noc : $keep}]
  }

  # Memory controller resources (<noc>/MC_<n>, <noc>/HBM<n>_PORT<m>) by the
  # NoC that owns them.
  proc mc_owner {resource} {
    if {[regexp {^(/.+)/(MC_\d+|HBM\d+_PORT\d+)$} $resource -> owner]} { return $owner }
    return ""
  }

  # Memory candidates: cells whose master side reaches a memory resource or
  # an external port with an aperture. The processor's own NoC is dropped:
  # a NoC with no memory controller of its own, whose AXI slave ports the
  # processor drives, and whose memory every other candidate already
  # offers (Master_NoC on the VEK385, which reaches the DDR NoCs and the
  # AIE over INI). AMD's versal_comn_platform declares the memory NoCs and
  # the aggregating NoC, not the PS's NoC. BRAM controllers are not
  # platform memory: AMD's MPSoC_Ext_Platform and Kria platforms declare the
  # PS ports, not the SmartConnect in front of the BRAM, so a candidate that
  # reaches only BRAM is dropped.
  proc memory_candidates {candidates} {
    set keep {}
    foreach c [add_port_terminals $candidates Master resources] {
      set res {}
      foreach r [dict get $c resources] {
        set pin [get_bd_intf_pins -quiet [file dirname $r]]
        if {$pin ne "" && [string match *:axi_bram_ctrl:* [get_property VLNV [get_bd_cells -of_objects $pin]]]} continue
        lappend res $r
      }
      dict set c resources $res
      if {[reaches_terminal $res]} { lappend keep $c }
    }
    set out {}
    foreach c $keep {
      set cell [dict get $c cell]
      set res [lsort -unique [dict get $c resources]]
      set owns 0
      foreach r $res { if {[mc_owner $r] eq $cell} { set owns 1 } }
      if {!$owns && [is_noc $cell] && [processor_drives $cell]} {
        set others {}
        foreach o $keep {
          if {[dict get $o cell] ne $cell} { lappend others {*}[dict get $o resources] }
        }
        set covered 1
        foreach r $res { if {$r ni $others} { set covered 0 } }
        if {$covered} continue
      }
      lappend out $c
    }
    return $out
  }

  # Memory type of a NoC's controllers: HBM, LPDDR or DDR. axi_noc names it
  # in CONFIG.CONTROLLERTYPE (DDR4_SDRAM, LPDDR4_SDRAM); axi_noc2 has no
  # such property, so the board interface in use names it
  # (CONFIG.C0_CH0_LPDDR5_BOARD_INTERFACE on the VEK385). Both list
  # parameters for every memory type, so parameter names alone say nothing.
  proc memory_type {noc} {
    set noc [get_bd_cells $noc]
    if {[get_property -quiet CONFIG.HBM_NUM_CHNL $noc] > 0} { return HBM }
    set ctype [get_property -quiet CONFIG.CONTROLLERTYPE $noc]
    if {$ctype ne ""} { return [expr {[string match -nocase *LPDDR* $ctype] ? "LPDDR" : "DDR"}] }
    foreach k [list_property $noc CONFIG.*_BOARD_INTERFACE] {
      if {[string match -nocase *LPDDR* $k] && [get_property $k $noc] ni {"" Custom}} { return LPDDR }
    }
    return DDR
  }

  # The NoC memory the processor maps at its lowest address (address 0 on
  # the VEK385, where its operating system runs): memory shared with the PS.
  proc ps_memory_cell {} {
    set best ""
    set best_off ""
    foreach ps [ps_cells] {
      foreach sp [get_bd_addr_spaces -quiet -of_objects [get_bd_cells $ps]] {
        foreach s [get_bd_addr_segs -quiet -of_objects $sp] {
          set off [string map {_ ""} [get_property -quiet OFFSET $s]]
          if {![string is wideinteger -strict $off]} continue
          if {$best_off ne "" && $off >= $best_off} continue
          foreach slave [get_bd_addr_segs -quiet -of_objects $s] {
            set cell [lindex [split $slave /] 1]
            if {$cell ne "" && [is_noc /$cell]} { set best /$cell; set best_off $off; break }
          }
        }
      }
    }
    return $best
  }

  # Role of a memory candidate:
  #   aie        every resource is the AI Engine array
  #   aggregate  a NoC with no controller of its own that reaches the
  #              controllers of two or more other NoCs
  #   controller a NoC that owns memory controllers
  #   other      SmartConnect paths, BRAM, external ports
  proc memory_role {c} {
    set cell [dict get $c cell]
    set res [lsort -unique [dict get $c resources]]
    set aie 1
    foreach r $res { if {![string match -nocase */AIE_ARRAY* $r]} { set aie 0 } }
    if {$aie && [llength $res]} { return aie }
    if {![is_noc $cell]} { return other }
    set owners [lsort -unique [lmap r $res {expr {[mc_owner $r] eq "" ? [continue] : [mc_owner $r]}}]]
    if {$cell in $owners} { return controller }
    if {[llength $owners] >= 2} { return aggregate }
    return other
  }

  # Clock candidates. ::vitis::pfm::get_clock_candidates returns only the
  # clock on each proc_sys_reset's slowest_sync_clk. A clock wizard's
  # MBUFGCE outputs <clk>_o1, <clk>_o2, ... are divided copies of one MMCM
  # output, synchronous to each other (UG1702 "--clock Options"), so a reset
  # synchronized to the slowest of them serves them all; AMD's
  # versal_comn_platform declares clk_out1_o1..o4 against the one
  # /proc_sys_reset_o4 on clk_out1_o4. Those siblings are added with that
  # reset. The reset must be on the slowest sibling (proc_sys_reset
  # slowest_sync_clk); otherwise the siblings stay unoffered. Candidates are
  # returned in clock wizard pin order, which AMD's CEDs use for clock IDs
  # ("Change IDs to clk_wiz pin order").
  proc clock_candidates {candidates} {
    set by_pin [dict create]
    foreach c $candidates { dict set by_pin [dict get $c resource clock] $c }
    foreach c $candidates {
      set clk [dict get $c resource clock]
      if {![regexp {^(.*)_o\d+$} [get_property NAME [get_bd_pins $clk]] -> base]} continue
      set cell [get_bd_cells [dict get $c cell]]
      set sibs [lsearch -all -inline -regexp [get_bd_pins -quiet -of_objects $cell -filter {DIR==O && TYPE==clk}] "/${base}_o\\d+\$"]
      set freq [get_property -quiet CONFIG.FREQ_HZ [get_bd_pins $clk]]
      if {![string is double -strict $freq]} continue
      set slowest 1
      foreach s $sibs {
        set f [get_property -quiet CONFIG.FREQ_HZ $s]
        if {[string is double -strict $f] && $f < $freq} { set slowest 0 }
      }
      if {!$slowest} continue
      foreach s $sibs {
        if {[dict exists $by_pin $s]} continue
        dict set by_pin $s [dict create cell [dict get $c cell] kind [dict get $c kind] resource [dict create clock $s reset [dict get $c resource reset]]]
      }
    }
    set order [lsort -dictionary [dict keys $by_pin]]
    return [lmap p $order {dict get $by_pin $p}]
  }

  # PFM.CLOCK status (UG1702 "--clock Options"): an MBUFGCE output cannot be
  # a reference clock for v++ clock generation, so it is fixed_non_ref; an
  # MMCM output through a BUFG is fixed.
  proc clock_status {clk} {
    set pin [get_bd_pins $clk]
    if {![regexp {^(.*)_o\d+$} [get_property NAME $pin] -> base]} { return fixed }
    set cell [get_bd_cells -of_objects $pin]
    set ports [split [get_property -quiet CONFIG.CLKOUT_PORT $cell] ,]
    set drives [split [get_property -quiet CONFIG.CLKOUT_DRIVES $cell] ,]
    set i [lsearch -exact $ports $base]
    if {$i >= 0 && $i < [llength $drives] && [lindex $drives $i] ne "MBUFGCE"} { return fixed }
    return fixed_non_ref
  }

  # ::vitis::pfm::set_clock writes only id, is_default and proc_sys_reset.
  # Add status to every declared PFM.CLOCK entry that has none.
  proc set_clock_status {} {
    foreach name [get_bd_cells -quiet -hierarchical] {
      set cell [get_bd_cells $name]
      set v [get_property -quiet PFM.CLOCK $cell]
      if {$v eq ""} continue
      foreach pin [dict keys $v] {
        if {![dict exists $v $pin status]} { dict set v $pin status [clock_status $cell/$pin] }
      }
      set_property PFM.CLOCK $v $cell
    }
  }

  # Default clock: the candidate closest to 300 MHz. v++ compiles HLS
  # kernels at the platform's default clock when no frequency is given (AR
  # 000035889), and AMD's platforms default to 312.5 MHz: versal_comn_platform
  # on VEK385, VCK190, VEK280 and VRK160 (clk_out1_o2), and the VCK190 base
  # platform (UG1702 "--clock Options" platforminfo example).
  proc default_clock {candidates} {
    set best 0
    set dist ""
    set i 0
    foreach c $candidates {
      set f [get_property -quiet CONFIG.FREQ_HZ [get_bd_pins [dict get $c resource clock]]]
      if {[string is double -strict $f]} {
        set d [expr {abs($f - 300e6)}]
        if {$dist eq "" || $d < $dist} { set best $i; set dist $d }
      }
      incr i
    }
    return $best
  }

  # Interrupt candidates whose pin is an input. When a PL interrupt
  # controller qualifies, the processor's own inputs (pl_ps_irq*,
  # pl_fpd_irq*, ...) are dropped: XD101 allows them only for simple designs,
  # and AMD's versal_comn_platform declares only /axi_intc_0.
  # XD101 "Add Interrupt Support": PFM.IRQ goes on the interrupt controller's
  # intr input (set_property PFM.IRQ {intr {id 0 range 32}} [get_bd_cells
  # /axi_intc_0]); its irq output drives the PS.
  proc interrupt_candidates {candidates} {
    set keep {}
    foreach c $candidates {
      set pin [get_bd_pins -quiet [dict get $c resource]]
      if {$pin ne "" && [get_property DIR $pin] eq "I"} { lappend keep $c }
    }
    set ps [ps_cells]
    set pl [lmap c $keep {expr {[get_bd_cells [dict get $c cell]] in $ps ? [continue] : $c}}]
    return [expr {[llength $pl] ? $pl : $keep}]
  }

  # IRQ range for an interrupt candidate: an axi_intc intr input takes up to
  # 32 interrupts (UG1701 "Adding Hardware Interfaces": "enable 32 IRQ
  # inputs to axi_intc_0 intr port", {intr {id 0 range 32}}); v++ sizes it
  # by parameter propagation at link time. Other pins take one.
  proc irq_range {c} {
    if {[string match {*:ip:axi_intc:*} [get_property VLNV [get_bd_cells [dict get $c cell]]]]} { return 32 }
    return 1
  }

  # After ::vitis::pfm::set_memory on a NoC, drop S<nn>_AXI slots at or
  # above NUM_PL_NMU + NUM_AIE_NMU. set_memory declares up to that plus
  # CONFIG.NUM_SI, and Vivado 2026.2 flags slots past the sum with
  # [BD 41-2940]. AMD's versal_comn_platform provisions each memory NoC "for
  # up to AIE_NMU + PL_NMU" (vek385_pfm_decls.tcl; S00..S55 on the VEK385).
  # Trimming from the top keeps the "no gaps in the port index numbering"
  # rule (UG1701 "AXI Ports").
  proc trim_noc_ports {noc_cell} {
    set model [get_noc_model]
    set limit [expr {[get_property NUM_PL_NMU $model] + [get_property NUM_AIE_NMU $model]}]
    set keep {}
    foreach {p a} [get_property PFM.AXI_PORT $noc_cell] {
      if {[scan $p {S%d_AXI} i] == 1 && $i >= $limit} continue
      lappend keep $p $a
    }
    set_property PFM.AXI_PORT $keep $noc_cell
    return [expr {[llength $keep] / 2}]
  }

  # Declare the Zynq UltraScale+ PS interfaces the design leaves unused, in
  # one set_property call: FPD HPM masters as control (HPM0_LPD only when
  # neither is free), HP/HPC slaves as memory
  # tagged MEMORY<first_memory>, MEMORY<first_memory+1>, ..., or all with
  # sptag when one is given. Returns the PFM.AXI_PORT value it set.
  #
  # UG1701 "Adding Hardware Interfaces":
  #  - control ports use memport M_AXI_GP; memory ports use S_AXI_HP or
  #    S_AXI_HPC with memory "<PS cell> <segment>", as in its ZynqMP example
  #    (S_AXI_HPC0_FPD {memport "S_AXI_HPC" sptag "HPC0"
  #    memory "zynq_ultra_ps_e_0 HPC0_DDR_LOW"});
  #  - "for each type of PFM interface, all ports are required to be set in a
  #    single set_property Tcl command";
  #  - "avoid declaring ports used within the platform itself", so connected
  #    interfaces are skipped;
  #  - sptag is user-defined, and "ports that share same SP Tag ID must be
  #    equivalent resources", so each slave gets its own tag.
  # XD101 "Enable AXI Interfaces for the Platform": keep sptag empty on the
  # M_AXI_GP masters ("sptag is only applicable to AXI slave interfaces").
  # XD101 "Add Interrupt Support": "Unused AXI interfaces can be exported in
  # the Platform Setup, irrespective of its visibility in the block diagram",
  # so interfaces the PS configuration disables (no pin) are declared; v++
  # enables them at link time. The same section uses M_AXI_HPM0_LPD for the
  # platform's own control (the interrupt controller) and reserves
  # M_AXI_HPM0_FPD and M_AXI_HPM1_FPD for kernels, as MPSoC_Ext_Platform does,
  # so HPM0_LPD is declared only when neither FPD master is free.
  proc declare_zynq_ps_ports {{first_memory 0} {sptag ""}} {
    set ps [get_bd_cells -hierarchical -filter {VLNV =~ *:zynq_ultra_ps_e:*}]
    if {[llength $ps] != 1} {
      error "expected one zynq_ultra_ps_e cell, found [llength $ps]"
    }
    set psn [get_property NAME $ps]
    set n $first_memory
    set masters {}
    foreach p {M_AXI_HPM0_FPD M_AXI_HPM1_FPD} {
      if {![connected $ps $p]} { lappend masters $p }
    }
    if {[llength $masters] == 0 && ![connected $ps M_AXI_HPM0_LPD]} {
      lappend masters M_AXI_HPM0_LPD
    }
    set ports {}
    foreach p $masters {
      lappend ports $p {memport "M_AXI_GP"}
    }
    foreach {p mp seg} {S_AXI_HPC0_FPD S_AXI_HPC HPC0 S_AXI_HPC1_FPD S_AXI_HPC HPC1
                        S_AXI_HP0_FPD S_AXI_HP HP0 S_AXI_HP1_FPD S_AXI_HP HP1
                        S_AXI_HP2_FPD S_AXI_HP HP2 S_AXI_HP3_FPD S_AXI_HP HP3} {
      if {[connected $ps $p]} continue
      lappend ports $p [list memport $mp sptag [expr {$sptag ne "" ? $sptag : "MEMORY$n"}] memory "$psn ${seg}_DDR_LOW"]
      incr n
    }
    set_property PFM.AXI_PORT $ports $ps
    return $ports
  }

  # Apply every delegated default (SKILL.md step 5) in one call: discover,
  # filter, declare. Memory is tagged by role (AIE, <TYPE><n> per controller
  # NoC from the processor's address-0 memory, the bare type on the
  # aggregating NoC, MEMORY<n> otherwise); one memory gets auto preferred,
  # the AIE and the PS-shared memory get auto false, and each NoC is trimmed
  # to NUM_PL_NMU + NUM_AIE_NMU slots. Control (a SmartConnect when one
  # qualifies, else every NoC that traces back to a processor) gets auto
  # preferred and no sptag (XD101: sptag "is only applicable to AXI slave
  # interfaces"). Clocks get IDs from 0 in clock wizard pin order, a status,
  # and is_default on the one closest to 300 MHz. Interrupts get IDs from 0,
  # range 32 on an axi_intc. Streams get sptag STREAM0, STREAM1, ... On Zynq
  # UltraScale+ the PS ports follow, tagged MEMORY<n>.
  # Options for a user who takes every resource but names tags or numbering:
  #   -memory_sptag <tag>       one sptag for every memory port, PS ports
  #                             included (UG1701: ports may share an sptag)
  #   -stream_sptag <tag>       one sptag for every stream port
  #   -clock_first_id <n>       first clock ID
  #   -default_clock <pin>      the default clock's pin path
  #   -interrupt_first_id <n>   first interrupt ID
  # UG1701 "Adding Hardware Interfaces": interrupt IDs are 0 to 127, so IDs
  # past 127 are an error rather than a silent truncation.
  # Returns a dict of category -> the cells it declared, PFM -> every cell's
  # PFM.* properties as set, and pfm_api -> bundled when autopfm.tcl supplied
  # the ::vitis::pfm commands, builtin when Vivado did.
  proc apply_defaults {args} {
    set opt [dict create -memory_sptag "" -stream_sptag "" -clock_first_id 0 -default_clock "" -interrupt_first_id 0]
    if {[llength $args] % 2} { error "apply_defaults: options come in -name value pairs" }
    foreach {k v} $args {
      if {![dict exists $opt $k]} { error "apply_defaults: unknown option $k; valid: [dict keys $opt]" }
      dict set opt $k $v
    }
    foreach k {-clock_first_id -interrupt_first_id} {
      if {![string is integer -strict [dict get $opt $k]] || [dict get $opt $k] < 0} {
        error "apply_defaults: $k must be a non-negative integer"
      }
    }
    set memory    [memory_candidates [::vitis::pfm::get_memory_candidates]]
    set control   [control_candidates [::vitis::pfm::get_control_candidates]]
    set clocks    [clock_candidates [::vitis::pfm::get_clock_candidates]]
    set irqs      [interrupt_candidates [::vitis::pfm::get_interrupt_candidates]]
    set streams   [::vitis::pfm::get_stream_candidates]
    set last_irq [expr {[dict get $opt -interrupt_first_id] - 1}]
    foreach c $irqs { incr last_irq [irq_range $c] }
    if {[llength $irqs] && $last_irq > 127} {
      error "[llength $irqs] interrupt candidates from ID [dict get $opt -interrupt_first_id] would reach ID $last_irq; IDs are limited to 0-127, so ask the user which to declare"
    }
    set mtag [dict get $opt -memory_sptag]
    set applied [dict create memory {} control {} clock {} interrupt {} stream {} zynq_ps {}]

    # Memory sptag and auto, after AMD's versal_comn_platform
    # (vek385_pfm_decls.tcl, vck_vek_pfm_decls.tcl): AIE for the AI Engine
    # array, auto false "to force user to opt in to access AIE memory-map";
    # <TYPE><n> per controller NoC, the memory holding the processor's
    # address 0 first (DDR0, LPDDR1, LPDDR2 on VCK190), auto false on that
    # one "to force user to opt in to share accelerator w/PS"; the bare type
    # for the NoC that aggregates them (LPDDR), auto preferred. When there is
    # no aggregating NoC, the first controller not shared with the PS is
    # preferred; with nothing else, the first candidate is.
    set ps_mem [ps_memory_cell]
    set roles [dict create]
    foreach c $memory { dict set roles [dict get $c cell] [memory_role $c] }
    set ctrl_cells [lsort -dictionary [lmap c $memory {expr {[dict get $roles [dict get $c cell]] eq "controller" ? [dict get $c cell] : [continue]}}]]
    if {$ps_mem in $ctrl_cells} {
      set ctrl_cells [linsert [lsearch -all -inline -not -exact $ctrl_cells $ps_mem] 0 $ps_mem]
    }
    set preferred ""
    foreach c $memory { if {[dict get $roles [dict get $c cell]] eq "aggregate"} { set preferred [dict get $c cell]; break } }
    if {$preferred eq ""} {
      foreach cell $ctrl_cells { if {$cell ne $ps_mem} { set preferred $cell; break } }
    }
    if {$preferred eq "" && [llength $memory]} {
      foreach c $memory { if {[dict get $roles [dict get $c cell]] ne "aie"} { set preferred [dict get $c cell]; break } }
    }
    set n 0
    foreach c $memory {
      set cell [dict get $c cell]
      set role [dict get $roles $cell]
      switch $role {
        aie        { set tag AIE }
        controller { set tag "[memory_type $cell][lsearch -exact $ctrl_cells $cell]" }
        aggregate  {
          set types [lsort -unique [lmap r [dict get $c resources] {expr {[mc_owner $r] eq "" ? [continue] : [memory_type [mc_owner $r]]}}]]
          set tag [expr {[llength $types] == 1 ? [lindex $types 0] : "MEMORY"}]
        }
        default    { set tag "MEMORY$n"; incr n }
      }
      set instr [dict create sptag [expr {$mtag ne "" ? $mtag : $tag}]]
      if {$cell eq $preferred} {
        dict set instr auto preferred
      } elseif {$role eq "aie" || ($cell eq $ps_mem && $preferred ne "")} {
        dict set instr auto false
      }
      ::vitis::pfm::set_memory $c $instr
      if {[is_noc $cell]} { trim_noc_ports [get_bd_cells $cell] }
      dict lappend applied memory $cell
    }
    foreach c $control {
      ::vitis::pfm::set_control $c [dict create auto preferred]
      dict lappend applied control [dict get $c cell]
    }
    set first [dict get $opt -clock_first_id]
    set default_idx [default_clock $clocks]
    if {[dict get $opt -default_clock] ne ""} {
      set want [dict get $opt -default_clock]
      set default_idx [lsearch -exact [lmap c $clocks {dict get $c resource clock}] $want]
      if {$default_idx < 0} { error "apply_defaults: -default_clock $want is not a clock candidate" }
    }
    set i 0
    foreach c $clocks {
      ::vitis::pfm::set_clock $c [dict create id [expr {$first + $i}] is_default [expr {$i == $default_idx ? "true" : "false"}]]
      dict lappend applied clock [dict get $c cell]
      incr i
    }
    set_clock_status
    set i [dict get $opt -interrupt_first_id]
    foreach c $irqs {
      set r [irq_range $c]
      ::vitis::pfm::set_interrupt $c [expr {$r > 1 ? [dict create id $i range $r] : [dict create id $i]}]
      dict lappend applied interrupt [dict get $c cell]
      incr i $r
    }
    set stag [dict get $opt -stream_sptag]
    set i 0
    foreach c $streams {
      ::vitis::pfm::set_stream $c [dict create sptag [expr {$stag ne "" ? $stag : "STREAM$i"}]]
      dict lappend applied stream [dict get $c cell]
      incr i
    }
    if {[llength [get_bd_cells -quiet -hierarchical -filter {VLNV =~ *:zynq_ultra_ps_e:*}]]} {
      declare_zynq_ps_ports $n $mtag
      dict set applied zynq_ps [get_bd_cells -hierarchical -filter {VLNV =~ *:zynq_ultra_ps_e:*}]
    }
    set pfm {}
    foreach cell [get_bd_cells -hierarchical] {
      foreach p [list_property $cell PFM.*] {
        set v [get_property $p $cell]
        if {$v ne ""} { lappend pfm [list $cell $p $v] }
      }
    }
    dict set applied PFM $pfm
    dict set applied pfm_api [expr {[info exists ::vitis_platform::pfm_api] ? "bundled" : "builtin"}]
    return $applied
  }

  proc connected {cell intf} {
    set pin [get_bd_intf_pins -quiet $cell/$intf]
    expr {$pin ne "" && [get_bd_intf_nets -quiet -of_objects $pin] ne ""}
  }
}

# Load the ::vitis::pfm commands. Vivado 2026.2 defines them when IP
# Integrator initializes (a block design is open); earlier releases get the
# bundled copy, which would replace the shipped procedures if sourced over
# them. Sourcing this file returns builtin or bundled.
if {![llength [info commands ::vitis::pfm::get_memory_candidates]]} {
  source [file join [file dirname [file normalize [info script]]] autopfm.tcl]
}
if {![llength [info commands ::vitis::pfm::get_memory_candidates]]} {
  error "the ::vitis::pfm commands are not defined after sourcing autopfm.tcl"
}
# is_noc (autopfm.tcl and the 2026.2 sdsoc_pfm.tcl alike) runs get_property
# on ::bd::utils::get_parent's result, a plain cell path, which does not
# always resolve; get_memory_candidates then fails with [Common 17-161] on
# a BRAM controller (seen on 2026.1).
if {[llength [info procs ::vitis::pfm::utils::is_noc]] && ![llength [info commands ::vitis::pfm::utils::is_noc_object]]} {
  rename ::vitis::pfm::utils::is_noc ::vitis::pfm::utils::is_noc_object
  proc ::vitis::pfm::utils::is_noc {inst} {
    set cell [get_bd_cells -quiet $inst]
    ::vitis::pfm::utils::is_noc_object [expr {$cell eq "" ? $inst : $cell}]
  }
}
expr {[info exists ::vitis_platform::pfm_api] ? "bundled" : "builtin"}
