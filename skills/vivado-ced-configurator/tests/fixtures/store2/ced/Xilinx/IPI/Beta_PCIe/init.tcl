# Synthetic fixture modelled on the store's init.tcl layout.
proc getSupportedParts {} {
  return [get_parts {xcvp1202-vsva2785-2MP-e-S}]
}

proc getSupportedBoards {} {
}

proc addOptions {DESIGNOBJ PROJECT_PARAM.BOARD_PART} {
    lappend x [dict create name "CPM_Config" type "string" \
        value "CPM4" \
        value_list {CPM4 CPM5} \
        enabled true]
    lappend x [dict create name "CPM5_Preset" type "string" value "Gen4x8_MM" enabled true \
        value_list {Gen4x8_MM Gen5x8_MM_Performance}]
    lappend x [dict create name "LANE_RATE" type "string" value "32.0_GT/s" value_list {16.0_GT/s 32.0_GT/s} enabled true]
    return $x
}

gui_updater {PROJECT_PARAM.BOARD_PART} {CPM_Config.VISIBLE CPM_Config.ENABLEMENT CPM_Config.VALUE} {
if { [regexp "vck190" ${PROJECT_PARAM.BOARD_PART}]} {
set CPM_Config.ENABLEMENT false
set CPM_Config.VALUE CPM4
}
}

validater {LANE_RATE.VALUE} {LANE_RATE.ERRMSG} {
  set LANE_RATE.ERRMSG ""
}
