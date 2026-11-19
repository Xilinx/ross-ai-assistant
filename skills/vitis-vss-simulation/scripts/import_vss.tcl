# import_vss.tcl — reusable VSS → Vivado import script (vss-import stage)
#
# Usage (run from COMPILED_OUT_DIRECTORY; the .vss is at <vss_dir>/<VSS_IMPORT>.vss):
#   vivado -mode batch -source <skill-dir>/scripts/import_vss.tcl \
#          -tclargs <PRJ_NAME> <PART> <VSS_IMPORT> [VSS_DIR]
#
#   PRJ_NAME    Vivado project name         (e.g. vss_prj)
#   PART        target part                 (e.g. xcvc1902-vsva2197-2MP-e-S)
#   VSS_IMPORT  basename of the .vss        (e.g. vss_ss  -> <VSS_DIR>/vss_ss.vss)
#   VSS_DIR     dir holding the .vss        (pass the absolute path; default: vss)

set prj_name   [lindex $argv 0]
set part_name  [lindex $argv 1]
set VSS_IMPORT [lindex $argv 2]
set vss_dir    [expr {$argc > 3 ? [lindex $argv 3] : "vss"}]

# All paths below are absolute: the project lives under the launch directory.
set out_dir  [file normalize [pwd]]
set prj_dir  [file join $out_dir $prj_name]
set vss_file [file normalize [file join $vss_dir ${VSS_IMPORT}.vss]]
set bd_file  [file join $prj_dir ${prj_name}.srcs sources_1 bd ${VSS_IMPORT} ${VSS_IMPORT}.bd]

create_project -force $prj_name $prj_dir -part $part_name

# import the .vss
vitis::import_vss $vss_file
update_compile_order -fileset sources_1

open_bd_design $bd_file
save_bd_design
validate_bd_design

# create wrapper
make_wrapper -files [get_files $bd_file] -top -import
update_compile_order -fileset sources_1
