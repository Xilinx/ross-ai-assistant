# Generic offline database exporter. Arguments are data, never recipes.
# Invoke with -log <absolute-output-root>/native.log and -tclargs root part hints...
namespace eval ::ipdb_export {}
set ::ipdb_export::library [file normalize [file join [file dirname [info script]] .. lib lite.tcl]]
source -notrace $::ipdb_export::library
proc ::ipdb_export::run {export_root part hints} {
 if {[file pathtype $export_root] ne "absolute" || [string match /home/* [file normalize $export_root]]} {error "EXPORT_FAIL:absolute scratch root outside /home required"}
 puts [lite_ced::init $export_root $part [file join $export_root native.log]]
proc ::ipdb_export::b64 {v} {return [binary encode base64 -maxlen 0 [encoding convertto utf-8 $v]]}
set out [open $export_root/catalog.tsv w]
puts $out "HEADER\t[b64 [version]]\t[b64 [get_property PART [current_project]]]"
foreach name $hints {
 if {[regexp {^[A-Za-z0-9_]+$} $name]} {
  set pattern xilinx.com:ip:${name}:*
 } elseif {[regexp {^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$} $name]} {
  set pattern $name
 } else {error "EXPORT_FAIL:use catalog IP names or exact VLNVs"}
 set defs [get_ipdefs -all -quiet $pattern]
 if {![llength $defs]} {puts $out "MISSING\t[b64 $name]";continue}
 foreach def $defs {
  set code [catch {lite_ced::metadata $def} native]
  set xml [get_property XML_FILE_NAME $def]
  puts $out "IP\t[b64 $def]\t[b64 $xml]\t$code\t[b64 $native]"
  if {!$code} {
   dict for {p block} [lite_ced::parameter_blocks [dict get $native file]] {
    set vals [list $def $p [lite_ced::field $block {Display Name}] [lite_ced::field $block Type] [lite_ced::field $block {Default Value}] [lite_ced::field $block Description] [ipcfg_ced::param_range [dict get $native file] $p] $block]
    set enc {};foreach v $vals {lappend enc [b64 $v]};puts $out "PARAM\t[join $enc \t]"
   }
  }
  flush $out;puts "CATALOG:$def:NATIVE:$code"
 }
}
 close $out
 close_project
}
if {$argc < 3} {error "EXPORT_FAIL:arguments root part IP-name-or-exact-VLNV ..."}
::ipdb_export::run [lindex $argv 0] [lindex $argv 1] [lrange $argv 2 end]
exit
