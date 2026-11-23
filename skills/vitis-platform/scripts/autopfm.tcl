# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# ::vitis::pfm commands for Vivado releases before 2026.2.
# Below this header the file is a verbatim copy of the namespace eval ::vitis
# block at the end of scripts/ipintegrator/sdsoc_pfm.tcl in Vivado 2026.2,
# which defines the same commands when IP Integrator initializes. Source this
# file only when [info commands ::vitis::pfm::get_memory_candidates] is empty
# with a block design open; sourcing it over the shipped procedures replaces
# them.

namespace eval ::vitis_platform { variable pfm_api bundled }

namespace eval ::vitis {
  namespace eval pfm {
    namespace eval utils {
      proc decompose_vlnv { vlnv } {
        set s [split $vlnv ":"]
        set v [split [lindex $s 3] "."]
        return [dict create \
          vendor [lindex $s 0] \
          library [lindex $s 1] \
          name [lindex $s 2] \
          version [dict create major [lindex $v 0] minor [lindex $v 1]]]
      }
      proc get_ps {} {
        set ps_types [list \
          "*:ip:versal_cips:*" \
          "*:ip:ps_wizard:*" \
          "*:ip:psx_wizard:*" \
          ]
        set vlnv_filter [lmap x ${ps_types} {string cat "VLNV=~${x}"}]
        set whole_filter [join ${vlnv_filter} " || "]
        return [get_bd_cells -hierarchical -filter ${whole_filter}]
      }
      proc is_noc {inst} {
        set valid_cell_vlnv [list \
          "*:ip:axi_noc:*" \
          "*:ip:axi_noc2:*" \
          "*:ip:axi_noc3:*" \
          ]
        set vlnv [get_property VLNV ${inst}]
        foreach filter ${valid_cell_vlnv} {
          if {[string match ${filter} ${vlnv}]} {
            return true
          }
        }
        return false
      }
      proc get_noc_segs {inst} {
        set ret [list]
        set ddr_cnt [get_property -quiet CONFIG.NUM_MCP ${inst}]
        set hbm_cnt [get_property -quiet CONFIG.HBM_NUM_CHNL ${inst}]
        set hbm_str [get_property -quiet CONFIG.HBM_START_CHNL ${inst}]
        for {set ddr 0} {${ddr} < ${ddr_cnt}} {incr ddr} {
          lappend ret "MC_${ddr}"
        }
        for {set hbm 0} {${hbm} < ${hbm_cnt}} {incr hbm} {
          for {set i 0} {${i} < 4} {incr i} {
            lappend ret "HBM[expr ${hbm} + ${hbm_str}]_PORT${i}"
          }
        }
        return ${ret}
      }
      proc bridge_scan {intf} {
        set mode_invert [dict create Master Slave Slave Master]
        set ret [list]
        set local_mode [get_property MODE ${intf}]
        if {![dict exists ${mode_invert} ${local_mode}]} {
          return ${ret}
        }
        set peer_mode [dict get ${mode_invert} ${local_mode}]
        foreach peer_intf [find_bd_objs -thru_hier -relation connected_to ${intf}] {
          if {${peer_mode} != [get_property MODE ${peer_intf}]} {
            continue
          }
          lappend ret ${peer_intf}
          set peer [get_bd_cells -quiet -of_objects ${peer_intf}]
          if {[string length ${peer}] == 0} {
            continue
          }
          foreach bridge [split [get_property BRIDGES ${peer_intf}] ":"] {
            set bridge_intf [get_bd_intf_pins ${peer}/${bridge}]
            set ret [concat ${ret} [bridge_scan ${bridge_intf}]]
          }
        }
        return ${ret}
      }
      proc pin_trace {pin} {
        set passthru_set [list \
          "xilinx.com:ip:xlconcat:*" \
          "xilinx.com:ip:xlslice:*" \
          "xilinx.com:ip:util_reduced_logic:*" \
          "xilinx.com:ip:util_vector_logic:*" \
          "xilinx.inline_hdl:*:*" \
          ]
        set ret [list]
        set dir [get_property DIR ${pin}]
        lappend ret ${pin}
        foreach ep [find_bd_objs -relation connected_to ${pin}] {
          if {[get_property CLASS ${ep}] != "bd_pin"} {
            continue
          }
          set parent [::bd::utils::get_parent ${ep}]
          if {[lsearch -glob ${passthru_set} [get_property VLNV ${parent}]] == -1} {
            continue
          }
          set next_pins [get_bd_pins -of_object ${parent} -filter "DIR==${dir}"]
          foreach p ${next_pins} {
            set ret [concat ${ret} [pin_trace ${p}]]
          }
        }
        return ${ret}
      }
      proc dict_get_fb {d k v} {
        if {[dict exists ${d} ${k}]} {
          return [dict get ${d} ${k}]
        }
        return ${v}
      }
      proc list_length_compare {a b} {
        return [expr [llength ${a}] - [llength ${b}]]
      }
    }
    proc get_control_candidates {} {
      set port_vlnv "*:interface:aximm_rtl:*"
      set ret [list]
      set ps [utils::get_ps]
      set ctrl_set [list]
      if {[llength ${ps}] > 0} {
        set ctrl_set [get_bd_intf_pins -quiet -filter "MODE==Master&&VLNV=~${port_vlnv}" -of_objects ${ps}]
      } else {
        set ctrl_set [get_bd_intf_ports -quiet -filter "MODE==Slave&&VLNV=~${port_vlnv}"]
      }
      set valid_cell_vlnv [list \
        "*:ip:smartconnect:*" \
        "*:ip:axi_noc:*" \
        "*:ip:axi_noc2:*" \
        "*:ip:axi_noc3:*" \
        ]
      set vlnv_cell_eq [lmap x ${valid_cell_vlnv} {string cat "VLNV=~${x}"}]
      set whole_cell_filter [join ${vlnv_cell_eq} " || "]
      foreach cell [get_bd_cells -hierarchical -filter ${whole_cell_filter}] {
        set reached_by [list]
        foreach intf [get_bd_intf_pins -quiet -of_objects ${cell} -filter "MODE==Slave"] {
          foreach ep [utils::bridge_scan ${intf}] {
            if {[lsearch ${ctrl_set} ${ep}] >= 0} {
              lappend reached_by ${ep}
            }
          }
        }
        if {[llength ${reached_by}] > 0} {
          set vlnv [utils::decompose_vlnv [get_property VLNV ${cell}]]
          lappend ret [dict create \
            cell ${cell} \
            kind [dict get ${vlnv} name] \
            resource ${reached_by}]

        }
      }
      return ${ret}
    }
    proc get_memory_candidates {} {
      set ret [list]

      set noc_mem_config [dict create]
      foreach mem_noc [get_bd_cells -quiet -hierarchical -filter "CONFIG.NUM_MCP>0||CONFIG.HBM_NUM_CHNL>0"] {
        dict set noc_mem_config ${mem_noc} [utils::get_noc_segs ${mem_noc}]
      }

      set valid_cell_vlnv [list \
        "*:ip:smartconnect:*" \
        "*:ip:axi_noc:*" \
        "*:ip:axi_noc2:*" \
        "*:ip:axi_noc3:*" \
        ]
      set vlnv_cell_eq [lmap x ${valid_cell_vlnv} {string cat "VLNV=~${x}"}]
      set whole_cell_filter [join ${vlnv_cell_eq} " || "]
      set cell_reach_set [list]
      foreach cell [get_bd_cells -hierarchical -filter ${whole_cell_filter}] {
        set cell_reach [list]
        foreach intf [get_bd_intf_pins -of_objects ${cell} -filter "MODE==Master"] {
          set cell_reach [concat ${cell_reach} [utils::bridge_scan ${intf}]]
        }
        lappend cell_reach_set ${cell}
        lappend cell_reach_set ${cell_reach}
      }
      set cell_reach_set [lsort -decreasing -stride 2 -index 1 -command utils::list_length_compare ${cell_reach_set}]
      set seen_set [list]
      foreach {cell reach_set} ${cell_reach_set} {
        set resources [list]
        if {[dict exists ${noc_mem_config} ${cell}]} {
          foreach mem [dict get ${noc_mem_config} ${cell}] {
            lappend resources "${cell}/${mem}"
          }
        }
        foreach ep ${reach_set} {
          set port [get_bd_intf_ports -quiet ${ep}]
          set pin [get_bd_intf_pins -quiet ${ep}]
          if {[string length ${port}]} {
            lappend resources ${port}
          } else {
            set parent [::bd::utils::get_parent ${pin}]
            if {[utils::is_noc ${parent}]} {
              if {[dict exists ${noc_mem_config} ${parent}]} {
                set mems [dict get ${noc_mem_config} ${parent}]
                set cons [get_property CONFIG.CONNECTIONS ${pin}]
                foreach {con stats} ${cons} {
                  if {[lsearch ${mems} ${con}] >= 0} {
                    lappend resources "${parent}/${con}"
                  }
                }
              }
            } else {
              foreach x [get_bd_addr_segs -quiet -of_objects ${ep}] {
                set usage [get_property USAGE ${x}]
                if {![string match -nocase ${usage} "memory"]} {
                  continue
                }
                lappend resources ${x}
              }
            }
          }
        }
        if {[llength ${resources}] == 0} {
          continue
        }
        set resources [lsort ${resources}]
        if {[lsearch ${seen_set} ${resources}] >= 0} {
          continue
        }
        lappend seen_set ${resources}
        set vlnv [utils::decompose_vlnv [get_property VLNV ${cell}]]
        lappend ret [dict create \
          cell ${cell} \
          kind [dict get ${vlnv} name] \
          resources ${resources}]
      }
      return ${ret}
    }
    proc get_interrupt_candidates {} {
      set ret [list]
      set test_set [concat \
        [get_bd_pins -quiet -hierarchical -filter "TYPE==intr && INTF==false"] \
        [get_bd_ports -quiet -filter "INTF==false"]]
      foreach pin ${test_set} {
        set ep_set [utils::pin_trace ${pin}]
        foreach ep ${ep_set} {
          if {[llength [find_bd_objs -relation connected_to ${ep}]] == 0} {
            set cell [::bd::utils::get_parent ${ep}]
            set vlnv [utils::decompose_vlnv [get_property VLNV ${cell}]]
            lappend ret [dict create \
              cell ${cell} \
              kind [dict get ${vlnv} name] \
              resource ${ep}]
          }
        }
      }
      return ${ret}
    }
    proc get_clock_candidates {} {
      set ret [list]
      set psr_cells [get_bd_cells -quiet -hierarchical -filter "VLNV=~*:ip:proc_sys_reset:*"]
      foreach psr ${psr_cells} {
        set psr_clk [get_bd_pins ${psr}/slowest_sync_clk]
        set clk [find_bd_objs -relation connected_to -thru_hier ${psr_clk}]
        if {[llength ${clk}] == 0} {
          continue
        }
        set clk_cell ""
        set clk_cell_kind ""
        if {[get_property CLASS ${clk}] == "bd_pin"} {
          set clk_cell [::bd::utils::get_parent ${clk}]
          set vlnv [utils::decompose_vlnv [get_property VLNV ${clk_cell}]]
          set clk_cell_kind [dict get ${vlnv} name]
        }
        lappend ret [dict create \
          cell ${clk_cell} \
          kind ${clk_cell_kind} \
          resource [dict create clock ${clk} reset ${psr}]]
      }
      return ${ret}
    }
    proc get_stream_candidates {} {
      set ret [list]
      foreach intf [get_bd_intf_pins -quiet -hierarchical -filter "VLNV=~*:interface:axis_rtl:*"] {
        if {[llength [find_bd_objs -quiet -relation connected_to ${intf}]] == 0} {
          set cell [::bd::utils::get_parent ${intf}]
          set vlnv [utils::decompose_vlnv [get_property VLNV ${cell}]]
          lappend ret [dict create \
            cell ${cell} \
            kind [dict get ${vlnv} name] \
            resource ${intf}]
        }
      }
      return ${ret}
    }
    proc set_control {elem instr} {
      set pfm_args [dict create]
      if {[dict exists ${instr} "sptag"]} {
        dict set pfm_args "sptag" [dict get ${instr} "sptag"]
      }
      if {[dict exists ${instr} "auto"]} {
        dict set pfm_args "auto" [dict get ${instr} "auto"]
      }
      set target [get_bd_cells [dict get ${elem} cell]]
      set config [dict create]
      set init [get_property -quiet PFM.AXI_PORT ${target}]
      foreach {port port_args} ${init} {
        if {[string match "M*_AXI" ${port}]} {
          continue
        }
        dict set config ${port} ${port_args}
      }
      set port_limit 16
      if {[utils::is_noc ${target}]} {
        if {![catch {get_noc_model} noc_model]} {
          set port_limit [expr {[get_property NUM_PL_NSU ${noc_model}] + [get_property CONFIG.NUM_MI ${target}]}]
        }
      }
      for {set p 0} {${p} < ${port_limit}} {incr p} {
        set p_name "M[format %02d ${p}]_AXI"
        set exist_test [get_bd_intf_pins -quiet ${target}/${p_name}]
        if {[string length ${exist_test}] > 0} {
          continue
        }
        dict set config ${p_name} ${pfm_args}
      }
      set_property PFM.AXI_PORT ${config} ${target}
    }
    proc set_memory {elem instr} {
      set pfm_args [dict create]
      set target_hbm false
      if {[dict exists ${instr} "sptag"]} {
        dict set pfm_args "sptag" [dict get ${instr} "sptag"]
      }
      if {[dict exists ${instr} "auto"]} {
        dict set pfm_args "auto" [dict get ${instr} "auto"]
      }
      if {[dict exists ${instr} "hbm"]} {
        set target_hbm [dict get ${instr} "hbm"]
      }
      set target [get_bd_cells [dict get ${elem} cell]]
      set config [dict create]
      set init [get_property -quiet PFM.AXI_PORT ${target}]
      foreach {port port_args} ${init} {
        if {${target_hbm}} {
          if {[string match "HBM*_AXI" ${port}]} {
            continue
          }
        } else {
          if {[string match "S*_AXI" ${port}]} {
            continue
          }
        }
        dict set config ${port} ${port_args}
      }
      set port_limit 16
      if {[utils::is_noc ${target}]} {
        if {![catch {get_noc_model} noc_model]} {
          if {${target_hbm}} {
            set port_limit [get_property NUM_HBM_NMU ${noc_model}]
          } else {
            set port_limit [expr {[get_property NUM_PL_NMU ${noc_model}] + \
              [get_property NUM_AIE_NMU ${noc_model}] + \
              [get_property CONFIG.NUM_SI ${target}]}]
          }
        }
      }
      for {set p 0} {${p} < ${port_limit}} {incr p} {
        if {${target_hbm}} {
          set p_name "HBM[format %02d ${p}]_AXI"
        } else {
          set p_name "S[format %02d ${p}]_AXI"
        }
        set exist_test [get_bd_intf_pins -quiet ${target}/${p_name}]
        if {[string length ${exist_test}] > 0} {
          continue
        }
        dict set config ${p_name} ${pfm_args}
      }
      set_property PFM.AXI_PORT ${config} ${target}
    }
    proc set_interrupt {elem instr} {
      set pfm_args [dict create]
      if {[dict exists ${instr} "id"]} {
        dict set pfm_args "id" [dict get ${instr} "id"]
      }
      if {[dict exists ${instr} "range"]} {
        dict set pfm_args "range" [dict get ${instr} "range"]
      }
      set target [get_bd_cells [dict get ${elem} cell]]
      set pin [::bd::utils::get_short_name [dict get ${elem} resource]]
      set existing [get_property -quiet PFM.IRQ ${target}]
      dict set existing ${pin} ${pfm_args}
      set_property PFM.IRQ ${existing} ${target}
    }
    proc set_clock {elem instr} {
      set pfm_args [dict create]
      if {[dict exists ${instr} "id"]} {
        dict set pfm_args "id" [dict get ${instr} "id"]
      }
      if {[dict exists ${instr} "is_default"]} {
        dict set pfm_args "is_default" [dict get ${instr} "is_default"]
      }
      set target [dict get ${elem} cell]
      set resource [dict get ${elem} resource]
      set pin [dict get ${resource} clock]
      set pin [::bd::utils::get_short_name ${pin}]
      set psr [dict get ${resource} reset]
      dict set pfm_args "proc_sys_reset" ${psr}
      set existing [get_property -quiet PFM.CLOCK ${target}]
      dict set existing ${pin} ${pfm_args}
      set_property PFM.CLOCK ${existing} ${target}
    }
    proc set_stream {elem instr} {
      set pfm_args [dict create]
      if {[dict exists ${instr} "sptag"]} {
        dict set pfm_args "sptag" [dict get ${instr} "sptag"]
      }
      set target [dict get ${elem} cell]
      set intf [dict get ${elem} resource]
      set intf [::bd::utils::get_short_name ${intf}]
      set existing [get_property -quiet PFM.AXIS_PORT ${target}]
      dict set existing ${intf} ${pfm_args}
      set_property PFM.AXIS_PORT ${existing} ${target}
    }
  }
}

