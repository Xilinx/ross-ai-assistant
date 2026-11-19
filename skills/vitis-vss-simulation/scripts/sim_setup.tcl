# sim_setup.tcl — reusable testbench-add + simulator-aware sim-script generation (vss-sim-scripts stage)
#
# Usage (run from the dir that contains the Vivado project):
#   vivado -mode batch -source <skill-dir>/scripts/sim_setup.tcl \
#          -tclargs <PRJ_NAME> <TB_TOP_MODULE> <SIMULATOR> \
#                   <LIB_MAP_PATH> <GCC_INSTALL_PATH> <INSTALL_PATH> \
#                   <SIM_RUNTIME> <TB_FILE1> [TB_FILE2 ...]
#
#   PRJ_NAME          Vivado project name                          (e.g. vss_prj)
#   TB_TOP_MODULE     top testbench MODULE name                    (e.g. vss_tb)
#   SIMULATOR         xsim | questa | vcs | riviera | xcelium
#   LIB_MAP_PATH      precompiled Xilinx sim libraries (-lib_map_path)    (non-xsim; pass N/A for xsim)
#   GCC_INSTALL_PATH  simulator gcc bin (-gcc_install_path)              (non-xsim; pass N/A for xsim)
#   INSTALL_PATH      simulator tool bin (-install_path)                 (non-xsim; pass N/A for xsim)
#   SIM_RUNTIME       how long the simulation may run: all | a time such as 10us   (applied for every simulator)
#   TB_FILE...        one or more testbench source files            (e.g. ./vss_tb.v ./send_data.v)

set prj_name         [lindex $argv 0]
set tb_top           [lindex $argv 1]
set simulator        [lindex $argv 2]
set lib_map_path     [lindex $argv 3]
set gcc_install_path [lindex $argv 4]
set install_path     [lindex $argv 5]
set sim_runtime      [lindex $argv 6]
set tb_files         [lmap f [lrange $argv 7 end] {file normalize $f}]

# All paths are absolute: the project lives under the launch directory.
set out_dir [file normalize [pwd]]
open_project [file join $out_dir $prj_name ${prj_name}.xpr]

# Needed for AIE VSS simulations: without it an AIE-only VSS simulation ended at
# time 0 fs (no testbench activity, rc 0, no ERROR lines) when tested.
set_param bd.noc.empty_sim_for_dangling_nsu_nmu true
if {[get_param bd.noc.empty_sim_for_dangling_nsu_nmu] != 1} {
    error "bd.noc.empty_sim_for_dangling_nsu_nmu is not set to true"
}
puts "bd.noc.empty_sim_for_dangling_nsu_nmu = [get_param bd.noc.empty_sim_for_dangling_nsu_nmu]"

# add the testbench to the simulation fileset
add_files -fileset sim_1 -norecurse $tb_files
set_property top $tb_top [get_filesets sim_1]
update_compile_order -fileset sim_1

# per-simulator target + switches. Only questa sets extra options here; the others
# just select the target simulator.
# Add per-simulator {<sim>.simulate.<tool>.more_options} here if a run needs them.
switch -- $simulator {
    xsim {
        set_property target_simulator Xsim [current_project]
    }
    vcs {
        set_property target_simulator VCS [current_project]
    }
    riviera {
        set_property target_simulator Riviera [current_project]
    }
    xcelium {
        set_property target_simulator Xcelium [current_project]
    }
    questa {
        set_property target_simulator Questa [current_project]
        set_property -name {questa.elaborate.vopt.more_options} -value {-noscelab} -objects [get_filesets sim_1]
        set_property sim.ip.auto_export_scripts.target_simulator_only true [current_project]
    }
    default {
        error "Unsupported SIMULATOR '$simulator' (expected: xsim | questa | vcs | riviera | xcelium)"
    }
}

# Vivado stops the run at 1000ns by default, for every simulator; apply the requested run time.
set_property -name "${simulator}.simulate.runtime" -value $sim_runtime -objects [get_filesets sim_1]

# generate simulation scripts without launching the simulator.
# xsim needs no extra paths; the third-party simulators take the user-supplied
# precompiled-library / gcc / tool paths (the skill never hardcodes these).
if {$simulator eq "xsim"} {
    launch_simulation -scripts_only
} else {
    launch_simulation -scripts_only \
        -lib_map_path     $lib_map_path \
        -gcc_install_path $gcc_install_path \
        -install_path     $install_path
}
