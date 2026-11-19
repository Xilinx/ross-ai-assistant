# Synthetic fixture modelled on the store's init.tcl layout.
set currentFile [file normalize [info script]]
variable currentDir [file dirname $currentFile]

source -notrace "$currentDir/alpha_design.tcl"

proc getSupportedParts {} {
	 return ""
}

proc getSupportedBoards {} {
   return [get_board_parts -filter {(PART_NAME =~"*xczu*" && VENDOR_NAME=="xilinx.com")} -latest_file_version]
}

proc addOptions {DESIGNOBJ PROJECT_PARAM.BOARD_PART} {
   # lappend x [dict create name "Old" type "string" value "x" value_list {"x"} enabled true]
   if {[regexp small ${PROJECT_PARAM.BOARD_PART}]} {
	lappend x [dict create name "Preset" type "string" value "Microcontroller" value_list {"Microcontroller Microcontroller___Suitable_for_baremetal"} enabled true]
   } else {
	lappend x [dict create name "Preset" type "string" value "Microcontroller" value_list { "Microcontroller Microcontroller___Suitable_for_baremetal"   "Application_Processor Application_______Embedded_linux_capable"} enabled true]
   }
	lappend x [dict create name "Include_DDR" type "bool" value "false" enabled true]
	lappend x [dict create name "Quad" type "string" value [lindex $quads 0] value_list $quads enabled true]
	return $x
}

updater {PROJECT_PARAM.BOARD_PART Preset.VALUE} {Image.IMAGE_PATH Preset.ENABLEMENT} {
  set Preset.ENABLEMENT true
}

gui_updater {PROJECT_PARAM.BOARD_PART} {Include_DDR.VISIBLE Include_DDR.ENABLEMENT Include_DDR.VALUE} {
  set Include_DDR.ENABLEMENT true
  set Include_DDR.VALUE false
}

updater {Preset.VALUE} {Include_DDR.VALUE} {
  if {${Preset.VALUE} eq "Microcontroller"} {set Include_DDR.VALUE false}
}
