"""Behavioral Tcl and MCP tests. Run with cwd, TMPDIR and Python caches in scratch."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[2]
UPSTREAM = SKILL / 'lib/ipcfg/ipcfg.tcl'
sys.path.insert(0, str(SKILL / 'scripts'))
from structured_config import run_root  # noqa: E402
spec = importlib.util.spec_from_file_location('guard_mcp', SKILL / 'scripts/guard_mcp.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
MOCK = r'''
namespace eval mock {
 variable cells {}; variable drop ""; variable writes 0; variable saved 0; variable unsafe 0
 variable defaults {CONFIG.FIFO_DEPTH 512 CONFIG.TDATA_NUM_BYTES 1 CONFIG.FIFO_MODE 1 CONFIG.IS_ACLK_ASYNC 0 CONFIG.HAS_TLAST 0 CONFIG.MODE_CONFIG {known 0 keep 7}}
}
proc get_bd_cells {args} {
 set c /[string trimleft [lindex $args end] /]
 if {[dict exists $::mock::cells $c]} {return $c};return {}
}
proc list_property {cell} {return [dict keys [dict get $::mock::cells $cell]]}
proc get_property {key cell} {return [dict get $::mock::cells $cell $key]}
proc save_bd_design {} {incr ::mock::saved}
proc ipcfg::create_cell {vlnv cell} {
 dict set ::mock::cells /[string trimleft $cell /] [dict merge $::mock::defaults [dict create VLNV $vlnv]]
 return SUCCESS:created
}
proc ipcfg::resolve_vlnv {hint} {return VLNV:xilinx.com:ip:${hint}:1.0}
proc ipcfg::apply_dict {cell config} {
 incr ::mock::writes
 dict for {key val} $config {if {$key ne $::mock::drop} {dict set ::mock::cells $cell $key $val}}
 return SUCCESS:applied
}
proc ipcfg::capture {path script expected} {
 set code [catch {uplevel 1 $script} msg]
 set ok [expr {!$code && !$::mock::unsafe}]
 set receipt [dict create status [expr {$ok ? "OK" : "FAIL"}] excerpt $msg]
 ipcfg::write_evidence $path $receipt
 return $receipt
}
set ::lite::root $::env(TMPDIR)
file mkdir [file join $::lite::root evidence]
set r {{id depth text {requested depth 1024} key CONFIG.FIFO_DEPTH wanted 1024 doc PG085}}
'''


def run_tcl(body):
    with tempfile.TemporaryDirectory() as tmp:
        script = f'source {{{UPSTREAM}}}\nsource {{{SKILL / "lib/lite.tcl"}}}\n' + MOCK + '\n' + body
        path = Path(tmp) / 'case.tcl'
        path.write_text('if {[catch {\n' + script + '\n} result options]} {puts stderr $result;puts stderr [dict get $options -errorinfo];exit 1}\n')
        return subprocess.run(['tclsh', str(path)], cwd=tmp, env=dict(os.environ, TMPDIR=tmp),
                              capture_output=True, text=True, timeout=15)


class LiteTests(unittest.TestCase):
    def test_the_only_supported_ip_of_a_category_is_the_stand_in(self):
        """versal_cips on a Versal Gen2 part has no successor name; ps_wizard is the one
        supported IP of its category. Without naming it, Haiku built pcie_phy_versal from
        the task's words (ip-configurator-test-kit_29). Several category members stay a list."""
        result = run_tcl(r'''
proc lite::part_available {vlnv} {return WRONG_PART}
proc lite::supported_examples {vlnv} {return {supported_count 3 example_parts {}}}
proc lite::alternatives_live {vlnv} {return {}}
proc lite::same_category_live {vlnv} {
    if {$vlnv eq "v:ip:cips:1.0"} {return {{vlnv v:ip:ps_wizard name ps_wizard display_name {PS Wizard} description d}}}
    return {{vlnv v:ip:a name a display_name A description d} {vlnv v:ip:b name b display_name B description d}}
}
foreach v {v:ip:cips:1.0 v:ip:many:1.0} {
    set row [lindex [dict get [lrange [lite::candidate_availability [list $v]] 1 end] rows] 0]
    puts [list $v [expr {[dict exists $row alternatives] ? [dict get $row alternatives] : "none"}]]
}
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()[-2:]
        self.assertIn('vlnv v:ip:ps_wizard display_name {PS Wizard} match same_category', lines[0])
        self.assertTrue(lines[1].endswith('none'), lines[1])

    def test_live_stand_ins_follow_the_release_database_rule_without_companions(self):
        """2026.1 ships no IP database, so a NoC request on a Versal Gen2 part found no axi_noc2
        stand-in and ended unconfigured or on SmartConnect (ip-configurator-test-kit_25/26). The
        live rule offers a supported successor (name + generation number) or an IP with the same
        display name, never a companion such as versal_cips_ps_vip."""
        result = run_tcl(r'''
proc lite::_need_ipcfg {} {}
set ::catalog {
    v:ip:axi_noc:1.1 {AXI NoC} 0   v:ip:axi_noc2:1.1 {AXI NoC2} 1
    v:ip:versal_cips:3.4 {CIPS} 0  v:ip:versal_cips_ps_vip:1.0 {CIPS VIP} 1
    v:ip:clk_wizard:1.0 {Clocking Wizard} 0  v:ip:clkx5_wiz:1.0 {Clocking Wizard} 1  v:ip:clk_wiz:6.0 {Clocking Wizard} 0
}
proc get_ipdefs {args} {
    set pattern [lindex $args end]
    set out {}
    foreach {vlnv display ok} $::catalog {if {[string match $pattern $vlnv]} {lappend out $vlnv}}
    return $out
}
proc get_property {args} {
    set out {}
    foreach object [lindex $args end] {foreach {vlnv display ok} $::catalog {if {$vlnv eq $object} {lappend out $display}}}
    return [expr {[llength [lindex $args end]] == 1 ? [lindex $out 0] : $out}]
}
proc ipcfg::part_status {def {part ""}} {foreach {vlnv display ok} $::catalog {if {$vlnv eq $def} {return [expr {$ok ? "AVAILABLE" : "WRONG_PART"}]}}}
foreach request {v:ip:axi_noc:1.1 v:ip:versal_cips:3.4 v:ip:clk_wizard:1.0} {puts [list $request [lite::alternatives_live $request]]}
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()[-3:]
        self.assertIn('vlnv v:ip:axi_noc2 display_name {AXI NoC2} match name_family', lines[0])
        self.assertEqual(lines[1], 'v:ip:versal_cips:3.4 {}')
        self.assertIn('vlnv v:ip:clkx5_wiz display_name {Clocking Wizard} match display_name', lines[2])
        self.assertNotIn('clk_wiz:', lines[2])

    def test_begin_does_not_borrow_vivados_implicit_in_memory_project(self):
        """A project query before begin makes Vivado open "New Project" (directory ".", default
        part, never saved). Borrowing it put a stock-MCP run's cell in a design that never reached
        disk (ip-configurator-test-kit_05); a saved project or one holding a design is kept."""
        result = run_tcl(r'''
proc get_filesets {args} {return sources_1}
proc fake_project {name dir designs} {set ::fake [dict create DIRECTORY $dir NAME $name designs $designs]}
proc get_property {key project} {dict get $::fake $key}
proc get_files {args} {dict get $::fake designs}
fake_project {New Project} . {}
lappend out [lite::implicit_project {New Project}]
fake_project {New Project} . {design_1.bd}
lappend out [lite::implicit_project {New Project}]
close [open [file join $::env(TMPDIR) proj.xpr] w]
fake_project proj $::env(TMPDIR) {}
lappend out [lite::implicit_project proj]
proc get_property {args} {error "no such property"}
lappend out [lite::implicit_project anything]
puts $out
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split()[-4:], ['1', '0', '0', '0'])

    def test_native_cell_identity_is_a_valid_assertion(self):
        self.check('''ipcfg::create_cell xilinx.com:ip:generic:1.0 named_cell_0
set req [list [dict create id name text {name named_cell_0} key __CELL_NAME__ \
 wanted named_cell_0 doc native_bd_cell_identity mode assert]]
puts [lite::requirements_check $req]
puts [lite::readback /named_cell_0 $req]
''', '__CELL_NAME__', 'pass 1', 'actual named_cell_0', 'failures {}')

    def test_begin_adopts_matching_nonempty_open_design_as_borrowed(self):
        self.check('''set ::lite::root ""
set log [file join $::env(TMPDIR) workspace .vivado-ai native.log]
file mkdir [file dirname $log];close [open $log w]
ipcfg::create_cell xilinx.com:ip:generic:1.0 existing_0
rename get_bd_cells original_get_bd_cells
proc get_bd_cells {args} {
 if {[lindex $args end] eq "*"} {return [dict keys $::mock::cells]}
 return [original_get_bd_cells {*}$args]
}
proc current_project {args} {return project}
proc current_bd_design {args} {return nl_bd}
proc get_files {args} {return nl_bd.bd}
rename source original_source
proc source {args} {uplevel 1 [list original_source [lindex $args end]]}
rename get_property old_get_property
proc get_property {key object} {
 if {$object eq "project" && $key eq "PART"} {return part}
 if {$object eq "project" && $key eq "DIRECTORY"} {return /scratch/harness-project}
 return [old_get_property $key $object]
}
puts [lite::begin part $log]
puts "ROOT $::lite::root"
puts "CELLS [get_bd_cells -quiet *]"
''', 'INIT:ADOPTED', 'bd nl_bd', 'session_mode borrowed', 'ROOT ', '/workspace', '/existing_0')

    def test_missing_derived_leaf_uses_exact_retained_source_encoding(self):
        self.check('''dict set ::mock::cells /f [dict merge $::mock::defaults [dict create \
 CONFIG.D {MDB5_GT PCIe0_x2_10GbE}]]
set req [list [dict create id width text {PCIe x2} key CONFIG.D \
 subkey PCIE0_LINK_WIDTH wanted X2 doc source equivalent_key CONFIG.D \
 equivalent_subkey MDB5_GT equivalent_wanted PCIe0_x2_10GbE \
 equivalence_scope same_container_exact_value_and_identifier_tokens]]
set rb [lite::readback /f $req]
puts $rb
set ::lite::records [dict create /f [dict create requirements $req final_readback $rb]]
puts [lite::verified_readback /f]
''', 'failures {}', 'pass 1', 'actual PCIe0_x2_10GbE',
            'key CONFIG.D', 'subkey MDB5_GT', 'propagated 1')

    def test_missing_derived_leaf_rejects_nonmatching_source_encoding(self):
        self.check('''dict set ::mock::cells /f [dict merge $::mock::defaults [dict create \
 CONFIG.D {MDB5_GT PCIe0_x4_10GbE}]]
set req [list [dict create id width text {PCIe x2} key CONFIG.D \
 subkey PCIE0_LINK_WIDTH wanted X2 doc source equivalent_key CONFIG.D \
 equivalent_subkey MDB5_GT equivalent_wanted PCIe0_x2_10GbE \
 equivalence_scope same_container_exact_value_and_identifier_tokens]]
puts [lite::readback /f $req]
''', 'pass 0', 'missing nested key PCIE0_LINK_WIDTH')

    def test_failed_customizer_range_inspection_is_not_retried(self):
        self.check('''ipcfg::create_cell xilinx.com:ip:generic:1.0 f
set ::creates 0
proc version {} {return {2026.1 SW Build 1}}
proc current_project {args} {return project}
rename get_property old_get_property
proc get_property {key object} {
 if {$object eq "project" && $key eq "PART"} {return part}
 return [old_get_property $key $object]
}
proc create_ip {args} {incr ::creates}
proc get_ips {args} {return [lindex $args end]}
puts [lite::context_ranges /f {CONFIG.FIFO_DEPTH}]
puts [lite::context_ranges /f {CONFIG.FIFO_DEPTH} {CONFIG.FIFO_DEPTH 1024}]
puts "CREATES $::creates"
''', 'range_scope unavailable', 'CREATES 1')

    def test_provisional_scalar_probe_restores_and_exact_discard_removes_cell(self):
        self.check('''ipcfg::create_cell xilinx.com:ip:generic:1.0 f
proc set_property {key value cell} {dict set ::mock::cells $cell $key $value}
proc delete_bd_objs {cell} {dict unset ::mock::cells $cell}
puts [lite::probe_scalar f CONFIG.FIFO_DEPTH 1024]
puts "RESTORED [get_property CONFIG.FIFO_DEPTH /f]"
puts [lite::discard_cell f xilinx.com:ip:generic:1.0]
puts "ABSENT [llength [get_bd_cells -quiet f]]"
''', 'PROBE:OK', 'actual 1024', 'restored 512', 'RESTORED 512', 'DISCARD:OK', 'ABSENT 0')

    def test_explicit_grounded_enum_token_preserves_pinned_value_on_remap(self):
        self.check('''set old [list [dict create text {link x2} wanted X2]]
set now [list [dict create text {link x2} wanted LINK_x2_AUX covered_values x2]]
puts "COVERED:[expr {[lite::intent_change $old $now] eq {}}]"
set bad [list [dict create text {link x2} wanted LINK_x4_AUX]]
puts [lite::intent_change $old $bad]
''', 'COVERED:1', 'requested value X2')

    def test_published_part_gate_prevents_creation_but_keeps_unknown_explicit(self):
        self.check('''proc get_ipdefs {args} {return vendor:ip:feature:1.0}
proc current_project {args} {return project}
rename get_property old_get_property
set ::published_parts {supported-part}
proc get_property {args} {
 set key [lindex $args end-1]
 if {$key eq "SUPPORTED_PARTS"} {return $::published_parts}
 if {$key eq "PART"} {return current-part}
 return [old_get_property {*}$args]
}
puts [lite::catalog vendor:ip:feature:1.0]
puts "BLOCKED [catch {lite::open_cell feature f} reason] $reason"
set ::published_parts {}
# A real IP's published parts do not change within a session; forget the remembered answer to
# model an IP whose metadata is missing.
set ::ipcfg::part_memo {}
puts "MISSING [lite::part_available vendor:ip:feature:1.0]"
puts [lite::catalog vendor:ip:feature:1.0]
''', 'CATALOG:UNAVAILABLE', 'BLOCKED 1 DISCOVERY_FAIL:WRONG_PART', 'MISSING UNKNOWN', 'CATALOG:EXACT')

    def test_begin_retry_reuses_matching_project_without_resetting_ledger(self):
        self.check('''set log [file join $::env(TMPDIR) proj .vivado-ai native.log]
file mkdir [file dirname $log];close [open $log w]
proc current_project {args} {return project}
proc current_bd_design {args} {return design_1}
rename get_property old_get_property
proc get_property {key object} {
 if {$object eq "project" && $key eq "PART"} {return part}
 if {$object eq "project" && $key eq "DIRECTORY"} {return [file join $::env(TMPDIR) proj]}
 return [old_get_property $key $object]
}
set ::lite::records {original pinned}
puts [lite::begin part $log]
puts "PINNED [dict get $::lite::records original]"
puts "WRONG_PART [catch {lite::begin other $log}]"
puts "WRONG_BD [catch {lite::begin part $log other}]"
''', 'INIT:REUSED', 'PINNED pinned', 'WRONG_PART 1', 'WRONG_BD 1')

    def test_catalog_identity_ignores_vendor_and_version_search_tokens(self):
        self.check('''proc get_ipdefs {args} {return {}}
proc ipcfg::resolve_vlnv {hint} {
 puts "SEARCH_NAME $hint"
 return {VLNV_CANDIDATES:{vendor:ip:feature:2.0 vendor:ip:unrelated16550:1.0}}
}
puts [lite::catalog {Vendor|feature|1.0} 1]
puts [lite::catalog vendor:ip:feature:9.9 1]
''', 'SEARCH_NAME feature', 'shown {{vlnv vendor:ip:feature:2.0 matched_terms feature}}')

    def test_catalog_matches_display_names_and_drops_unmatched_filler(self):
        self.check('''set ::defs {v:ip:intc:4.0 v:ip:intc:4.1 v:ip:emb_gen:1.0 v:ip:bridge:3.0 v:ip:block_box:1.0}
set ::labels {{Interrupt Controller} {Interrupt Controller} {Embedded Memory Generator} {Bus Bridge} {Block Box}}
proc get_ipdefs {args} {if {[lindex $args end] eq "-quiet"} {return $::defs};return {}}
rename get_property old_get_property
proc get_property {args} {
 if {[lindex $args end-1] eq "DISPLAY_NAME"} {return $::labels}
 return [old_get_property {*}$args]
}
proc lite::part_available {vlnv} {return AVAILABLE}
proc ipcfg::resolve_vlnv {hint} {return {VLNV_CANDIDATES:{v:ip:block_box:1.0 v:ip:bridge:3.0}}}
puts [lite::catalog interrupt_controller]
puts [lite::catalog {Block Memory Generator}]
''', 'CATALOG:EXACT vlnv v:ip:intc:4.1',
            'shown {{vlnv v:ip:emb_gen:1.0 matched_terms {generator memory} display_name {embedded memory generator}} {vlnv v:ip:block_box:1.0 matched_terms block display_name {block box}}}')

    def test_catalog_prefers_the_rarer_matched_term(self):
        self.check('''set ::defs {v:ip:fam_codec:1.0 v:ip:mesh:1.0 v:ip:fam_a:1.0 v:ip:fam_b:1.0 v:ip:fam_c:1.0}
set ::labels {{Fam Codec} {AXI Mesh2} {Fam Alpha} {Fam Beta} {Fam Gamma}}
proc get_ipdefs {args} {if {[lindex $args end] eq "-quiet"} {return $::defs};return {}}
rename get_property old_get_property
proc get_property {args} {
 if {[lindex $args end-1] eq "DISPLAY_NAME"} {return $::labels}
 return [old_get_property {*}$args]
}
proc ipcfg::resolve_vlnv {hint} {return NONE}
proc lite::part_available {vlnv} {return AVAILABLE}
puts [lite::catalog {Fam Mesh} 2]
''', 'shown {{vlnv v:ip:mesh:1.0 matched_terms mesh display_name {axi mesh2}} {vlnv v:ip:fam_')

    def test_catalog_checks_compatibility_only_for_the_ranked_page(self):
        self.check('''proc ipcfg::resolve_vlnv {hint} {
 set defs {};for {set i 0} {$i<100} {incr i} {lappend defs vendor:ip:feature_$i:1.0}
 return "VLNV_CANDIDATES:{$defs}"
}
set ::checks {}
proc lite::part_available {vlnv} {lappend ::checks $vlnv;return AVAILABLE}
puts [lite::catalog feature 3]
puts "CHECKED [llength $::checks]"
''', 'matched 100', 'compatibility_checks 3', 'availability_scope ranked_page_only', 'CHECKED 3')

    def test_current_run_gui_getter_joins_native_range_and_rejects_stale_build(self):
        self.check('''set ::build one
proc version {} {return $::build}
proc current_project {} {return proj}
rename get_property original_get_property
proc get_property {p o} {if {$p eq "PART"} {return part};return [original_get_property $p $o]}
proc lite::resolve {hint} {return VLNV:vendor:ip:generic:1.0}
interp create ip_child
interp eval ip_child {
 namespace eval ipgui {}
 proc ipgui::current_instname {} {return native_instance}
 proc ipgui::current_inst {} {return native_handle}
 proc ipgui::get_guiparamspec {args} {return [lindex $args end]}
 proc set_property {p value key} {dict set ::actual $key $value}
 proc get_property {p key} {return [dict get $::actual $key]}
 set ::actual {}
}
puts [lite::watch_gui_help generic ip_child {CONFIG.WIDTH}]
interp eval ip_child {set_property tooltip {Actual GUI width help} WIDTH;set_property tooltip {Unselected} OTHER}
set f [file join $::env(TMPDIR) metadata.txt];set ch [open $f w]
puts $ch {Parameter Name: WIDTH
Parameter ID: PARAM_VALUE.WIDTH
Type: long
Range Minimum: 1 Maximum: 32
**********};close $ch
proc lite::metadata {hint} {if {[interp exists ip_child]} {interp delete ip_child};return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc lite::packaged_parameters {hint keys} {return {parameters {WIDTH {help {Packaged help}}}}}
puts [lite::describe generic {CONFIG.WIDTH}]
puts "SELECTED_COUNT [dict size [lite::selected_gui_help generic]]"
if {[interp exists ip_child]} {interp delete ip_child}
puts "CACHED_COUNT [dict size [lite::selected_gui_help generic]]"
set ::build two
puts "STALE_COUNT [dict size [lite::selected_gui_help generic]]"
''', 'HELP:WATCHING', 'help_source gui_tooltip', 'help {Actual GUI width help}', 'range RANGE_NUM:1:32', 'SELECTED_COUNT 1', 'CACHED_COUNT 1', 'STALE_COUNT 0')

    def test_exact_hidden_subcore_resolution_without_fuzzy_auto_selection(self):
        self.check('''proc get_ipdefs {args} {if {[lindex $args end] eq "vendor:ip:hidden:1.0"} {return vendor:ip:hidden:1.0};return {}}
puts [lite::resolve vendor:ip:hidden:1.0]
''', 'VLNV:vendor:ip:hidden:1.0')

    def test_describe_separate_keys_preserves_bounded_list_and_enum_paging(self):
        self.check('''proc lite::detail {hint keys subkeys offset} {return [list DETAIL:OK keys $keys offset $offset]}
puts [lite::describe generic {CONFIG.WIDTH CONFIG.MODE} 6]
puts [lite::describe generic CONFIG.WIDTH CONFIG.MODE 6]
puts "BAD [catch {lite::describe generic CONFIG.WIDTH stray}]"
puts "LIMIT [catch {lite::describe generic CONFIG.A CONFIG.B CONFIG.C CONFIG.D CONFIG.E}]"
puts "OFFSET [catch {lite::describe generic CONFIG.WIDTH -1}]"
''', 'keys {CONFIG.WIDTH CONFIG.MODE} offset 6', 'BAD 1', 'LIMIT 1', 'OFFSET 1')

    def test_combined_help_keeps_native_numeric_range_authoritative(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter Name: WIDTH
Parameter ID: PARAM_VALUE.WIDTH
Type: long
Range Minimum: 1 Maximum: 32
Default Value: 8
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc lite::packaged_parameters {hint keys} {return {parameters {WIDTH {help {Number of data bits}}}}}
puts [lite::describe generic {CONFIG.WIDTH}]
''', 'RANGE_NUM:1:32', 'range_source native_metadata', 'help_source packaged_description', 'help {Number of data bits}')

    def test_combined_packaged_fallback_pages_choices_and_marks_scope(self):
        self.check('''proc lite::metadata {hint} {error METADATA_UNAVAILABLE:no_managed_ip}
proc lite::packaged_parameters {hint keys} {
 set choices {};foreach i {0 1 2 3 4 5 6 7} {lappend choices [dict create value $i label Mode$i]}
 return [dict create vlnv vendor:ip:generic:1.0 parameters [dict create MODE [dict create label Mode type long default 0 help {Choose a mode} help_source packaged_description choices $choices]]]
}
puts [lite::describe generic {CONFIG.MODE CONFIG.MISSING}]
puts [lite::describe generic {CONFIG.MODE} 6]
puts "UNSAFE [catch {lite::describe generic {CONFIG.MODE;exit}}]"
''', 'DETAIL:PACKAGED', 'range_scope packaged_not_live', 'range_complete 0', 'native_only 1', 'more 1', 'offset 6', 'missing CONFIG.MISSING', 'UNSAFE 1')

    def test_combined_missing_help_or_range_remains_unavailable(self):
        self.check('''proc lite::metadata {hint} {error METADATA_UNAVAILABLE:no_managed_ip}
proc lite::packaged_parameters {hint keys} {return {vlnv generic parameters {WIDTH {label Width type long default 8 help {} help_source packaged_description choices {}}}}}
puts [lite::describe generic {CONFIG.WIDTH}]
''', 'help_source unavailable', 'range_source unavailable', 'RANGE_NONE')

    def test_packaged_parser_roundtrips_untrusted_text_as_data(self):
        self.check('''set f [file join $::env(TMPDIR) component.xml]
set ch [open $f w];puts $ch {<component xmlns="urn:ipxact"><choices><choice><name>modes</name><enumeration text="Off">0</enumeration><enumeration text="On">1</enumeration></choice></choices><parameters><parameter><name>MODE</name><displayName>Mode</displayName><description>Help [exit] $x; café &amp; units</description><value xmlns:s="urn:ipxact" s:format="long" s:choiceRef="modes">0</value></parameter></parameters><model><parameters><parameter><name>HIDDEN</name><description>Model only</description></parameter></parameters></model></component>};close $ch
proc lite::resolve {hint} {return VLNV:vendor:ip:generic:1.0}
proc get_ipdefs {args} {return definition}
proc version {} {return {2026.1 SW Build 1}}
proc current_project {} {return proj}
rename get_property mock_get_property
proc get_property {p o} {if {$p eq "XML_FILE_NAME"} {return [file join $::env(TMPDIR) component.xml]};if {$p eq "PART"} {return part};return [mock_get_property $p $o]}
set d [lite::packaged_parameters generic {CONFIG.MODE CONFIG.HIDDEN}]
puts [dict get $d parameters MODE help]
set second [lindex [dict get $d parameters MODE choices] 1]
puts "SECOND [dict get $second value] [dict get $second label]"
puts "MODEL_INCLUDED [dict exists $d parameters HIDDEN]"
set ::lite::dir /unrelated/native/framework
set d [lite::packaged_parameters generic {CONFIG.MODE}]
puts "STABLE_PATH [dict get $d parameters MODE default]"
''', 'Help [exit] $x; café & units', 'SECOND 1 On', 'MODEL_INCLUDED 0', 'STABLE_PATH 0')

    def check(self, body, *expected):
        result = run_tcl(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for text in expected:
            self.assertIn(text, result.stdout)
        return result.stdout

    def test_init_loads_upstream_globally_and_uses_native_log(self):
        self.check('''set ::lite::root ""
proc current_project {args} {return ""}
proc create_project {args} {return proj}
proc create_bd_design {args} {return design_1}
rename source original_source
proc source {args} {uplevel 1 [list original_source [lindex $args end]]}
set log [file join $::env(TMPDIR) native.log]
set ch [open $log w];close $ch
puts [lite::init $::env(TMPDIR) test-part $log]
puts "NESTED [namespace exists ::lite::ipcfg]"
puts "MODE $::ipcfg::mode"
puts "LOG [expr {$::ipcfg::native_log_path eq $log}]"''', 'INIT:OK', 'NESTED 0', 'MODE assemble', 'LOG 1')

    def test_generic_configuration_and_baseline(self):
        self.check('puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]\nputs [lite::finish f]\nputs "BASE [dict get $::lite::records /f baseline depth]"', 'CFG:OK', 'DONE:COVERAGE:FULL', 'BASE 512')

    def test_silent_revert_stops_without_full(self):
        out = self.check('''set ::mock::drop CONFIG.FIFO_DEPTH
puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]
puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]
puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]
puts [lite::finish f]
puts "WRITES $::mock::writes"''', 'CFG:STOP', 'DONE:COVERAGE:PARTIAL', 'WRITES 2')
        self.assertNotIn('DONE:COVERAGE:FULL', out)

    def test_requirements_immutable_and_baseline_preserved_on_repair(self):
        self.check('''set ::mock::drop CONFIG.FIFO_DEPTH
lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r
set r2 {{id depth text {requested depth 1024} key CONFIG.FIFO_DEPTH wanted 512 doc PG085}}
puts "REJECT [catch {lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 512} $r2}]"
set ::mock::drop ""
puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]
puts "BASE [dict get $::lite::records /f baseline depth]"''', 'REJECT 1', 'CFG:OK', 'BASE 512')

    def test_failed_apply_allows_remap_of_same_intent_only(self):
        self.check('''set ::mock::drop CONFIG.FIFO_DEPTH
lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r
set r2 {{id width text {requested depth 1024} key CONFIG.TDATA_NUM_BYTES wanted 1024 doc PG085}}
puts "VALUE [catch {lite::register /f {{id width text {requested depth 1024} key CONFIG.TDATA_NUM_BYTES wanted 4 doc PG085}}}]"
set ::mock::drop ""
puts [lite::configure axis_data_fifo f {CONFIG.TDATA_NUM_BYTES 1024} $r2]
puts "BASE [dict get $::lite::records /f baseline width]"
puts "AFTER [catch {lite::register /f $r}]"''', 'VALUE 1', 'CFG:OK', 'BASE 1', 'AFTER 1')

    def test_a_later_prompt_can_reconfigure_a_cell_after_reset(self):
        """'Change the FIFO depth to 2048' in a later prompt is a new intent for the same cell:
        without a reset the pinned ledger rejects it; reset_cell clears it, other cells keep theirs."""
        self.check('''lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r
dict set ::lite::records /other requirements kept
set later {{id depth text {depth 2048} key CONFIG.FIFO_DEPTH wanted 2048 doc PG085}}
puts "PINNED [catch {lite::register /f $later}]"
puts [lite::reset_cell f]
puts "LATER [catch {lite::register /f $later}]"
puts "OTHER [dict get $::lite::records /other requirements]"
puts [lite::reset_cell /never_configured]''', 'PINNED 1', 'RESET:OK cell /f', 'LATER 0', 'OTHER kept', 'RESET:NONE')

    def test_failed_apply_allows_encoding_change_the_text_does_not_name(self):
        self.check('''set ::mock::drop CONFIG.FIFO_DEPTH
set e {{id depth text {requested depth 1024} key CONFIG.FIFO_DEPTH wanted 1024 doc PG085} {id mode text {packet mode on} key CONFIG.FIFO_MODE wanted 1 doc PG085}}
lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024 CONFIG.FIFO_MODE 1} $e
set e2 {{id depth text {requested depth 1024} key CONFIG.FIFO_DEPTH wanted 1024 doc PG085} {id mode text {packet mode on} key CONFIG.FIFO_MODE wanted 2 doc PG085}}
puts "ENCODING [catch {lite::register /f $e2}]"
dict set ::lite::records /f remap_allowed 1
puts "DROPPED [catch {lite::register /f [lrange $e2 0 0]} msg] $msg"''', 'ENCODING 0', 'DROPPED 1', "requirement 'packet mode on' dropped")

    def test_library_reload_preserves_pinned_intent_and_retry_state(self):
        self.check('set ::mock::drop CONFIG.FIFO_DEPTH\nlite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r\n'
                   + f'source {{{SKILL / "lib/lite.tcl"}}}\n'
                   + 'puts "ATTEMPTS [dict get $::lite::records /f attempts]"\n'
                   + 'set r2 {{id depth text {requested depth 1024} key CONFIG.FIFO_DEPTH wanted 512 doc PG085}}\n'
                   + 'puts "REJECT [catch {lite::register /f $r2}]"', 'ATTEMPTS 1', 'REJECT 1')

    def test_final_readback_detects_drift(self):
        self.check('''lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r
dict set ::mock::cells /f CONFIG.FIFO_DEPTH 512
puts [lite::finish f]''', 'DONE:COVERAGE:PARTIAL', 'actual 512 wanted 1024')

    def test_empty_duplicate_unmapped_and_unsafe_capture(self):
        self.check('''puts "EMPTY [catch {lite::register /f {}}]"
puts "DUP [catch {lite::register /f [concat $r $r]}]"
set ::mock::unsafe 1
puts [lite::configure axis_data_fifo f {CONFIG.FIFO_DEPTH 1024} $r]
puts [lite::finish f]
puts "EMPTY_FINISH [catch {lite::finish f {}}]"
set missing {{id missing text {missing feature} key {} wanted true doc pending}}
puts [lite::configure axis_data_fifo other {CONFIG.FIFO_DEPTH 1024} $missing]
puts [lite::finish other]''', 'EMPTY 1', 'DUP 1', 'CFG:FIX', 'DONE:COVERAGE:PARTIAL', 'EMPTY_FINISH 1', 'unmapped requirement')

    def test_nested_merge_and_unknown_subkey_rejection(self):
        self.check('''set r {{id nested text {nested feature} key CONFIG.MODE_CONFIG subkey known wanted 1 doc fixture}}
puts [lite::configure axis_data_fifo f {CONFIG.MODE_CONFIG {known 1}} $r]
puts "KEPT [dict get [get_property CONFIG.MODE_CONFIG /f] keep]"
puts [lite::configure axis_data_fifo f {CONFIG.MODE_CONFIG {typo 1}} $r]''', 'CFG:OK', 'KEPT 7', 'unknown subkey')

    def test_generic_vector_updates_preserve_length_and_other_positions(self):
        self.check('''puts "VECTOR [lite::vector {100,100,100,100} {0 240 1 160 2 120}]"
puts "BOOL [lite::vector {false,false,false,false} {0 1 2 true} boolean]"
puts "EQ [expr {[lite::normal {250.000,200.000}] eq [lite::normal {250,200}]}]"
puts "SHORT [expr {[lite::normal {true}] eq [lite::normal {true,true,true,false}]}]"
puts "BOUNDS [catch {lite::vector {1,2} {2 3}}]"
puts "INVALID [catch {lite::vector {true,false} {0 yes} boolean}]"''', 'VECTOR 240,160,120,100', 'BOOL true,false,true,false', 'EQ 1', 'SHORT 0', 'BOUNDS 1', 'INVALID 1')

    def test_discovery_accepts_prose_commas_but_keeps_unknown_features(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter ID: PARAM_VALUE.WIDTH
Display Name: Transfer width
Type: long
Default Value: 8
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc ipcfg::dict_find {file keyword} {return DF:none}
puts [lite::inspect generic {width, absent,}]
puts "EMPTY_REJECT [catch {lite::inspect generic {,}}]"
puts "WILDCARD_REJECT [catch {lite::inspect generic {*,}}]"
''', 'keywords {width absent}', 'matched 1', 'key CONFIG.WIDTH', 'unmatched absent', 'EMPTY_REJECT 1', 'WILDCARD_REJECT 1')

    def test_metadata_selection_and_paging_without_ip_dispatch(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w]
puts $ch {Parameter Name: WIDTH_A
Parameter ID: PARAM_VALUE.WIDTH_A
Display Name: Data width A
Type: long
Default Value: 8
Range Minimum: 1 Maximum: 64
**********
Parameter Name: WIDTH_B
Parameter ID: PARAM_VALUE.WIDTH_B
Display Name: Data width B
Type: long
Default Value: 16
Range Minimum: 1 Maximum: 128
**********
Parameter Name: MODE
Parameter ID: PARAM_VALUE.MODE
Display Name: Operating mode
Type: string
Default Value: stream
Range List: stream Stream
            packet Packet
**********}
close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc ipcfg::dict_find {file keyword} {return DF:none}
puts [lite::inspect vendor:ip:anything:1.0 {width absent} 1]
puts [lite::inspect vendor:ip:anything:1.0 {width} 1 1]
puts "ALL_REJECT [catch {lite::inspect generic {*} }]"''', 'matched 2', 'shown 1 more 1', 'unmatched absent', 'key CONFIG.WIDTH_A', 'offset 1', 'key CONFIG.WIDTH_B', 'ALL_REJECT 1')


class MetadataTests(unittest.TestCase):
    def test_resolved_native_schema_exposes_fields_without_dumping_them_inline(self):
        self.check('''puts [lite::open_cell generic f]
set file [file join $::env(TMPDIR) native.txt]
set ch [open $file w];puts $ch {Parameter Name: MODE_CONFIG
Parameter ID: PARAM_VALUE.MODE_CONFIG
Current Value: known 0 keep 7
**********
Parameter Name: MODE_CONFIG_INTERNAL
Parameter ID: PARAM_VALUE.MODE_CONFIG_INTERNAL
Current Value: known 0 keep 7 published 0
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) native.txt] vlnv generic]}
set reply [lite::nested_schema generic f]
puts [lindex $reply 0]
set data [lrange $reply 1 end]
set ch [open [dict get $data file] r];set schema [dict get [read $ch] data];close $ch
puts "PUBLISHED_SCOPE [dict get $schema parameters CONFIG.MODE_CONFIG fields published mapping_scope]"
puts "KNOWN_SCOPE [dict get $schema parameters CONFIG.MODE_CONFIG fields known mapping_scope]"
puts "EXACT [lite::native_field_mapping generic CONFIG.MODE_CONFIG published CONFIG.MODE_CONFIG_INTERNAL]"
puts "RENAMED [lite::native_field_mapping generic CONFIG.MODE_CONFIG published CONFIG.OTHER_INTERNAL]"
set req {{id f text {enable discovered field} key CONFIG.MODE_CONFIG subkey published wanted 1 doc native mapping_scope native_same_name_candidate native_key CONFIG.MODE_CONFIG_INTERNAL}}
puts [lite::configure generic f {CONFIG.MODE_CONFIG {published 1}} $req]
puts "KEEP [dict get [get_property CONFIG.MODE_CONFIG /f] keep]"
''','NESTED:OK','PUBLISHED_SCOPE native_same_name_candidate','KNOWN_SCOPE live_dictionary','EXACT 1','RENAMED 0','CFG:OK','KEEP 7')

    def test_inner_dictionary_fields_are_offered_and_written_by_merging_requested_keys(self):
        self.check('''puts [lite::open_cell generic f]
dict set ::mock::cells /f CONFIG.MODE_CONFIG {known 0 port {ENABLE 0 IO A IO_TYPE MIO}}
set file [file join $::env(TMPDIR) native.txt]
set ch [open $file w];puts $ch {Parameter Name: MODE_CONFIG
Parameter ID: PARAM_VALUE.MODE_CONFIG
Current Value: known 0 port {ENABLE 0 IO A IO_TYPE MIO}
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) native.txt] vlnv generic]}
set reply [lite::nested_schema generic f]
set ch [open [dict get [lrange $reply 1 end] file] r];set schema [dict get [read $ch] data];close $ch
puts "SHAPE [dict get $schema parameters CONFIG.MODE_CONFIG fields port shape]"
set req {{id f text {enable port on B} key CONFIG.MODE_CONFIG subkey port wanted {ENABLE 1 IO B} doc native mapping_scope live_dictionary}}
puts [lite::configure generic f {CONFIG.MODE_CONFIG {port {ENABLE 1 IO B}}} $req]
puts "PORT [dict get [get_property CONFIG.MODE_CONFIG /f] port]"
''','SHAPE dictionary','CFG:OK','PORT ENABLE 1 IO B IO_TYPE MIO')

    def test_native_and_live_views_are_distinguished_in_one_inspection(self):
        self.check('''proc lite::inspect {args} {return {META:OK candidates {{key CONFIG.FIFO_DEPTH current 99} {key CONFIG.HIDDEN current 7}} evidence native-receipt}}
puts [lite::inspect_for generic f {depth}]
puts [lite::inspect_for generic f {depth}]
''', 'key CONFIG.FIFO_DEPTH current 512 bd_property_exists 1', 'key CONFIG.HIDDEN current 7 bd_property_exists 0', 'native managed-IP metadata')

    def test_resolved_dictionary_discovery_works_without_graph_nodes(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt];set ch [open $f w]
puts $ch {Parameter Name: RESOLVED
Parameter ID: PARAM_VALUE.RESOLVED
Type: string
Current Value: CLOCK_ZERO 100 CLOCK_ONE 250 OTHER 99
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc ipcfg::dict_find {file keyword} {return DF:none}
puts [lite::inspect generic {clock} 1]
''', 'matched 2 offset 0 shown RESOLVED<CLOCK_ONE> more 1')

    def test_native_resolved_key_requires_write_docs_and_reads_back_written_dict(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter Name: RESOLVED
Parameter ID: PARAM_VALUE.RESOLVED
Current Value: published 0 untouched 10
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc lite::known_subkeys {hint key} {return {}}
set requirements {{id n text {enable published option} key CONFIG.MODE_CONFIG subkey published wanted 1 doc {native selected dictionary entry} native_key CONFIG.RESOLVED write_doc {official writable dictionary mapping}}}
puts [lite::configure generic f {CONFIG.MODE_CONFIG {published 1}} $requirements]
puts [lite::finish f]
puts "KEEP [dict get [get_property CONFIG.MODE_CONFIG /f] keep]"
''', 'CFG:OK', 'DONE:COVERAGE:FULL', 'KEEP 7')

    def test_derived_key_needs_explicit_write_grounding_and_readback(self):
        self.check('''dict set ::mock::defaults CONFIG.DERIVED {renamed 0 unrelated 99}
proc lite::known_subkeys {hint key} {return {}}
set bare {{id n text {nested requested behavior} key CONFIG.MODE_CONFIG subkey new wanted 1 doc metadata read_key CONFIG.DERIVED read_subkey renamed}}
puts [lite::configure generic missing_ground {CONFIG.MODE_CONFIG {new 1}} $bare]
puts "NO_WRITES $::mock::writes"
set grounded [list [dict merge [lindex $bare 0] {write_doc {official configuration mapping fixture}}]]
rename ipcfg::apply_dict original_apply_dict
proc ipcfg::apply_dict {cell config} {
 original_apply_dict $cell $config
 dict set ::mock::cells $cell CONFIG.DERIVED renamed [dict get $config CONFIG.MODE_CONFIG new]
 return SUCCESS:applied
}
puts [lite::configure generic grounded {CONFIG.MODE_CONFIG {new 1}} $grounded]
puts [lite::finish grounded]
puts "KEEP [dict get [get_property CONFIG.MODE_CONFIG /grounded] keep]"
''', 'explicit write_doc', 'NO_WRITES 0', 'CFG:OK', 'DONE:COVERAGE:FULL', 'KEEP 7')

    def test_selected_receipt_does_not_expose_large_dictionary_defaults(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w]
puts $ch "Parameter Name: SETTINGS\nParameter ID: PARAM_VALUE.SETTINGS\nDisplay Name: Subsystem settings\nType: string\nDefault Value: [string repeat X 10000]\nCurrent Value: [string repeat Y 10000]\n**********"
close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc ipcfg::dict_find {file keyword} {return DF:none}
set reply [lite::inspect generic {settings}]
set ch [open [lindex $reply end] r];set public [read $ch];close $ch
puts "BOUNDED [expr {[string length $public]<2000}]"
puts "NO_RAW_DEFAULT [expr {[string first [string repeat X 200] $public]<0}]"
puts $reply
''', 'BOUNDED 1', 'NO_RAW_DEFAULT 1', 'default_truncated 1', 'current_truncated 1')

    def test_native_scalar_preflight_rejects_bad_values_before_write(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter Name: FIFO_DEPTH
Parameter ID: PARAM_VALUE.FIFO_DEPTH
Type: long
Range Minimum: 1 Maximum: 2048
**********};close $ch
proc lite::context_ranges {cell keys {proposed {}}} {
 set file [file join $::env(TMPDIR) metadata.txt];set rows {}
 foreach key $keys {dict set rows $key [dict create range [ipcfg::param_range $file [string range $key 7 end]] range_complete 1 range_file $file]}
 return $rows
}
puts [lite::configure generic f {CONFIG.FIFO_DEPTH 9999} $r]
puts [lite::configure generic f {CONFIG.FIFO_DEPTH nonsense} $r]
puts "NO_WRITES $::mock::writes"
puts [lite::configure generic f {CONFIG.FIFO_DEPTH 1024} $r]
''', 'VALUE_FAIL:VAL_OUT_OF_RANGE', 'expects a numeric value', 'NO_WRITES 0', 'CFG:OK')

    def test_native_enum_preflight_rejects_unknown_encoding(self):
        self.check('''dict set ::mock::defaults CONFIG.MODE off
set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter Name: MODE
Parameter ID: PARAM_VALUE.MODE
Type: string
Range List: off Disabled
            packet Packet mode
**********};close $ch
proc lite::context_ranges {cell keys {proposed {}}} {
 set file [file join $::env(TMPDIR) metadata.txt];set rows {}
 foreach key $keys {dict set rows $key [dict create range [ipcfg::param_range $file [string range $key 7 end]] range_complete 1 range_file $file]}
 return $rows
}
set requirements {{id m text {packet operation} key CONFIG.MODE wanted packet doc {native enum labels}}}
puts [lite::configure generic f {CONFIG.MODE packet_typo} $requirements]
puts "NO_WRITES $::mock::writes"
puts [lite::configure generic f {CONFIG.MODE packet} $requirements]
''', 'VALUE_FAIL:VAL_NOT_IN_LIST', 'NO_WRITES 0', 'CFG:OK')

    def test_live_fallback_is_bounded_paged_and_retains_nested_container(self):
        self.check('''proc ipcfg::find_params {cell keyword} {return {FP:NESTED container=CONFIG.SETTINGS pattern=clock n=3 {alpha beta gamma}}}
puts [lite::inspect_cell cell {clock} 1]
puts [lite::inspect_cell cell {clock} 1 1]
puts "WORDS [catch {lite::inspect_cell cell {a b c d e f g h i}}]"
''', 'shape NESTED matched 3 offset 0 names alpha more 1 container CONFIG.SETTINGS', 'offset 1 names beta', 'WORDS 1')

    def test_catalog_ambiguity_is_ranked_bounded_and_requires_choice(self):
        self.check('''proc ipcfg::resolve_vlnv {hint} {return {VLNV_CANDIDATES:{vendor:ip:axi_alpha:1.0 vendor:ip:axi_bram_ctrl:1.0 vendor:ip:axi_delta:1.0}}}
puts [lite::catalog {AXI BRAM Controller} 1]
puts [lite::catalog {AXI BRAM Controller} 1 1]
puts "CHOICE [catch {lite::resolve {AXI BRAM Controller}} msg] $msg"
''', 'matched 3 offset 0', 'vlnv vendor:ip:axi_bram_ctrl:1.0', 'more 1 choice_required 1', 'CHOICE 1 DISCOVERY_FAIL:CATALOG:CANDIDATES')

    def test_init_adopts_only_matching_empty_scratch_project(self):
        self.check('''set ::lite::root ""
set log [file join $::env(TMPDIR) native.log];set ch [open $log w];close $ch
proc current_project {args} {return precreated}
proc current_bd_design {args} {return ""}
proc get_files {args} {return {}}
rename get_property original_get_property
proc get_property {key obj} {
 if {$obj eq "precreated" && $key eq "DIRECTORY"} {return [file join $::env(TMPDIR) proj]}
 if {$obj eq "precreated" && $key eq "PART"} {return target-part}
 return [original_get_property $key $obj]
}
proc create_project {args} {error {must not recreate project}}
proc create_bd_design {args} {return design_1}
rename source original_source
proc source {args} {uplevel 1 [list original_source [lindex $args end]]}
puts "WRONG_PART [catch {lite::init $::env(TMPDIR) wrong-part $log}]"
puts [lite::init $::env(TMPDIR) target-part $log]
puts "REINIT [catch {lite::init $::env(TMPDIR) target-part $log}]"
''', 'WRONG_PART 1', 'INIT:OK', 'REINIT 1')

    def test_seeded_part_support_answers_without_reading_supported_parts(self):
        """Reading SUPPORTED_PARTS took ~100 s per definition (vcu on 2026.2); a seeded release
        table answers instead, and only for the part it was seeded for."""
        out = self.check('''proc current_project {args} {return proj}
proc get_property {args} {if {[lindex $args 0] eq "PART"} {return $::part};error "SUPPORTED_PARTS read"}
proc get_ipdefs {args} {error "catalog read"}
set ::part p1
puts [lite::seed_support p1 [dict create a:b:vcu:1.2 {WRONG_PART 109 {xa1 xc2 xq3}} a:b:gpio:2.0 {AVAILABLE 0 {}}]]
puts "VCU [lite::part_available a:b:vcu:1.2] [lite::supported_examples a:b:vcu:1.2 2]"
puts "GPIO [lite::part_available a:b:gpio:2.0]"
set ::part p2
puts "OTHER_PART [lite::part_available a:b:vcu:1.2]"
''', 'SUPPORT:SEEDED part p1 ips 2', 'VCU WRONG_PART supported_count 109 example_parts {xa1 xc2}', 'GPIO AVAILABLE')
        self.assertIn('OTHER_PART UNKNOWN', out)   # not seeded for p2: falls back to the live read

    def test_init_adopts_a_nonempty_design_so_prompts_can_build_it_up(self):
        """A design grows over several prompts: cells from earlier prompts are kept, and nothing
        is created over the open project or block design."""
        out = self.check('''set ::lite::root ""
set log [file join $::env(TMPDIR) native.log];set ch [open $log w];close $ch
proc current_project {args} {return precreated}
proc current_bd_design {args} {return design_1}
proc get_bd_cells {args} {return existing_cell}
proc get_files {args} {return {}}
proc get_property {key obj} {if {$key eq "DIRECTORY"} {return [file join $::env(TMPDIR) proj]};return target-part}
namespace eval ::ipcfg {}
proc ::ipcfg::set_mode {args} {}
rename source __real_source
proc source {args} {}
proc create_project {args} {puts CREATED_PROJECT}
proc create_bd_design {args} {puts CREATED_BD}
set result [lite::init $::env(TMPDIR) target-part $log]
rename source {};rename __real_source source
puts "RESULT [lindex $result 0] mode [dict get [lrange $result 1 end] session_mode]"
puts "ROOT_SET [expr {$::lite::root ne {}}]"
''', 'RESULT INIT:OK mode owned', 'ROOT_SET 1')
        self.assertNotIn('CREATED_', out)

    def test_init_reopens_a_saved_project_of_the_same_part_and_refuses_another(self):
        script = '''set ::lite::root ""
set log [file join $::env(TMPDIR) native.log];set ch [open $log w];close $ch
file mkdir [file join $::env(TMPDIR) proj];set ch [open [file join $::env(TMPDIR) proj proj.xpr] w];close $ch
set ::opened ""
proc current_project {args} {return $::opened}
proc open_project {path} {set ::opened reopened;puts OPENED}
proc close_project {args} {set ::opened ""}
proc current_bd_design {args} {return [expr {$::opened ne "" ? "design_1" : ""}]}
proc get_filesets {args} {return sources_1}
proc get_files {args} {return [file join $::env(TMPDIR) design_1.bd]}
proc open_bd_design {args} {puts OPENED_BD}
proc get_bd_cells {args} {return earlier_cell}
proc get_property {key obj} {if {$key eq "DIRECTORY"} {return [file join $::env(TMPDIR) proj]};return SAVED_PART}
namespace eval ::ipcfg {}
proc ::ipcfg::set_mode {args} {}
rename source __real_source
proc source {args} {}
proc create_project {args} {puts CREATED_PROJECT}
proc create_bd_design {args} {puts CREATED_BD}
puts "TRY [catch {lite::init $::env(TMPDIR) WANTED_PART $log} msg] $msg"
rename source {};rename __real_source source
'''
        out = self.check(script.replace('WANTED_PART', 'SAVED_PART'), 'OPENED', 'OPENED_BD', 'TRY 0 INIT:OK')
        self.assertNotIn('CREATED_', out)
        out = self.check(script.replace('WANTED_PART', 'other-part'),
                         'TRY 1 INIT_FAIL:existing project', 'targets SAVED_PART not other-part')
        self.assertNotIn('CREATED_', out)

    def test_known_graph_subkey_can_be_added_but_unpublished_key_cannot(self):
        self.check('''proc lite::known_subkeys {hint key} {return {known keep published}}
set requirements {{id n text {enable published feature} key CONFIG.MODE_CONFIG subkey published wanted 1 doc {native dictionary graph}}}
puts [lite::configure generic f {CONFIG.MODE_CONFIG {published 1}} $requirements]
puts "KEEP [dict get [get_property CONFIG.MODE_CONFIG /f] keep]"
puts [lite::configure generic f {CONFIG.MODE_CONFIG {unknown 1}} $requirements]
''', 'CFG:OK', 'KEEP 7', 'unknown subkey')

    def test_begin_derives_scratch_from_mcp_log_and_rejects_other_paths(self):
        self.check('''set ::lite::root ""
proc lite::init {root part log bd} {return [list ROOT $root PART $part LOG $log BD $bd]}
puts [lite::begin target-part /scratch/case/proj/.vivado-ai/session.log]
puts "LOG_REJECT [catch {lite::begin target-part /scratch/case/session.log}]"
''', 'ROOT /scratch/case PART target-part', 'LOG_REJECT 1')

    check = LiteTests.check
    def test_enum_labels_and_targeted_dictionary_details(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {Parameter Name: MODE
Parameter ID: PARAM_VALUE.MODE
Display Name: Operating mode
Type: string
Range List: 0 Off
            2 Packet mode
Current Value: off
**********
Parameter Name: SETTINGS
Parameter ID: PARAM_VALUE.SETTINGS
Type: string
Current Value: selected 25 unrelated 99
**********};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
puts [lite::detail generic {CONFIG.MODE CONFIG.SETTINGS} {selected}]
puts [lite::detail generic {CONFIG.NOT_PRESENT}]
puts "LIMIT [catch {lite::detail generic {CONFIG.A CONFIG.B CONFIG.C CONFIG.D CONFIG.E}}]"
''', '{2 {Packet mode}}', 'PD:selected=25', 'missing CONFIG.NOT_PRESENT', 'LIMIT 1')

    def test_metadata_cache_identity_changes_with_build_part_and_vlnv(self):
        self.check('''set ::mock::version {2026.1 SW Build 1}
set ::mock::part part-a;set ::mock::dumps 0
proc version {} {return $::mock::version}
proc current_project {args} {return project}
rename get_property original_get_property
proc get_property {key obj} {if {$key eq "PART"} {return $::mock::part};return [original_get_property $key $obj]}
proc ipcfg::param_deps {name file vlnv dir} {
 incr ::mock::dumps;set ch [open $file w];puts $ch metadata;close $ch;return PARAM_DEPS:$file
}
lite::metadata alpha;lite::metadata alpha
puts "CACHED $::mock::dumps"
set ::mock::version {2026.1 SW Build 2};lite::metadata alpha
set ::mock::part part-b;lite::metadata alpha
lite::metadata beta
puts "IDENTITY $::mock::dumps"
proc ipcfg::param_deps {args} {return PARAM_DEPS_NA}
puts "UNAVAILABLE [catch {lite::metadata missing} msg] $msg"
proc ipcfg::param_deps {name file vlnv dir} {
 set ch [open $file w];puts $ch {ERROR: Cannot initialize customizer (may not support batch mode).};close $ch;return PARAM_DEPS:$file
}
puts "ERRORDUMP [catch {lite::metadata gamma} msg] $msg"
''', 'CACHED 1', 'IDENTITY 4', 'UNAVAILABLE 1 METADATA_UNAVAILABLE:PARAM_DEPS_NA',
            'ERRORDUMP 1 METADATA_UNAVAILABLE:Cannot initialize customizer (may not support batch mode).')

    def test_gating_uses_direct_published_predecessors_and_paging(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {digraph G {
1[label="PARAM_VALUE.ENABLE_A"]
2[label="PORT_ENABLEMENT.OUT_A"]
3[label="PARAM_VALUE.ENABLE_B"]
4[label="PORT_ENABLEMENT.OUT_B"]
5[label="PARAM_VALUE.UNRELATED"]
1->2
3->4
5->1
}};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
puts [lite::gates generic {out} 1]
puts [lite::gates generic {out} 1 1]
puts [lite::gates generic {missing}]
''', 'matched 2 offset 0 shown 1 more 1', 'PE:OUT_A:ENABLE_A', 'offset 1 shown 1 more 0', 'PE:OUT_B:ENABLE_B', 'matched 0')

    def test_nested_discovery_is_bounded_and_paged(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt];set ch [open $f w];close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
proc ipcfg::dict_find {file keyword} {return {DF:OPTIONS<A> OPTIONS<B> OPTIONS<C>}}
puts [lite::inspect generic {feature} 1]
puts [lite::inspect generic {feature} 1 1]
''', 'matched 3 offset 0 shown OPTIONS<A> more 1', 'matched 3 offset 1 shown OPTIONS<B> more 1')

    def test_gating_ranking_keeps_exact_interface_and_separate_interrupt(self):
        self.check('''set f [file join $::env(TMPDIR) metadata.txt]
set ch [open $f w];puts $ch {digraph G {
1[label="PARAM_VALUE.SECOND_CHANNEL"]
2[label="PARAM_VALUE.INTERRUPT_PRESENT"]
3[label="BUSIF_ENABLEMENT.BUS2"]
4[label="PORT_ENABLEMENT.BUS2_IO_I"]
5[label="PORT_ENABLEMENT.BUS2_IO_O"]
6[label="PORT_ENABLEMENT.BUS2_IO_T"]
7[label="BUSIF_ENABLEMENT.IP2INTC_IRQ"]
1->3
1->4
1->5
1->6
2->7
}};close $ch
proc lite::metadata {hint} {return [dict create file [file join $::env(TMPDIR) metadata.txt] vlnv $hint]}
puts [lite::gates generic {BUS2 irq intc} 2]
''', 'BUS2 PE:BUS2:SECOND_CHANNEL', 'IP2INTC_IRQ PE:IP2INTC_IRQ:INTERRUPT_PRESENT', 'shown 2 more 1')


class FakeUpstream:
    def __init__(self, failed=True, long=False):
        self.calls = []
        self.failed, self.long = failed, long

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        data = {'exit_code': 1 if self.failed else 0, 'output': 'syntax error' if not self.long else 'X' * 10000}
        return guard.types.CallToolResult(content=[guard.types.TextContent(type='text', text=json.dumps(data))])


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def test_forgiving_structured_schema_is_no_larger_than_baseline(self):
        tool = next(t for t in guard.structured_definitions([])
                    if t.name == 'vivado_execute')
        schema = tool.inputSchema
        self.assertEqual(schema['required'], ['command'])
        self.assertEqual(schema['properties']['command'], {})
        baseline = {'type': 'object', 'properties': {
            'session_id': {'type': 'string'},
            'command': {'type': 'string', 'description': 'JSON object encoded as a string.'}},
            'required': ['session_id', 'command'], 'additionalProperties': False}
        self.assertLessEqual(len(json.dumps(schema, sort_keys=True)),
                             len(json.dumps(baseline, sort_keys=True)))

    async def test_object_command_and_omitted_outer_session_are_accepted(self):
        class State:
            initialized = False
            stage = 'new'
            async def request(self, request):
                self.request_seen = request
                return {'stage': 'ready', 'next_op': 'apply'}
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = '/scratch/project/.vivado-ai/log'
            state = State()
            g.structured_sessions['s'] = state
            result = await g.execute(FakeUpstream(False), {
                'command': {'op': 'status', 'session_id': 's'}})
            self.assertFalse(result.isError)
            self.assertEqual(state.request_seen, {'op': 'status'})

    async def test_string_command_can_carry_the_only_session_id(self):
        class State:
            initialized = False
            stage = 'new'
            async def request(self, request):
                self.request_seen = request
                return {'stage': 'ready', 'next_op': 'apply'}
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = '/scratch/project/.vivado-ai/log'
            state = State()
            g.structured_sessions['s'] = state
            result = await g.execute(FakeUpstream(False), {
                'command': json.dumps({'op': 'status', 'session_id': 's'})})
            self.assertFalse(result.isError)
            self.assertEqual(state.request_seen, {'op': 'status'})

    async def test_omitted_session_requires_exactly_one_owned_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            no_session = await g.execute(FakeUpstream(False), {'command': {'op': 'status'}})
            self.assertTrue(no_session.isError)
            g.started.update({'a': 'a.log', 'b': 'b.log'})
            many = await g.execute(FakeUpstream(False), {'command': {'op': 'status'}})
            self.assertTrue(many.isError)
            self.assertIn('exactly one', many.content[0].text)

    async def test_fault_trace_repetition_gets_guidance_then_partial_finish(self):
        class State:
            initialized = True
            stage = 'discovering'
            terminal_stop = False
            vlnv = 'xilinx.com:ip:axi_gpio:2.0'
            def state(self): return {'stage': self.stage, 'next_op': 'select'}
            async def request(self, request):
                raise ValueError('waive requires mode=waive')
            async def finish_partial(self, reason):
                self.saved = True
                return {'stage': 'finished', 'full': False, 'next_op': 'begin'}
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = 'log'
            state = State()
            g.structured_sessions['s'] = state
            command = {'op': 'select', 'session_id': 's', 'selections': [
                {'requirement_id': 'r3', 'candidate_id': 'r3', 'reason': 'context'}]}
            first = await g.execute(FakeUpstream(False), {'command': command})
            second = await g.execute(FakeUpstream(False), {'command': command})
            third = await g.execute(FakeUpstream(False), {'command': command})
            self.assertTrue(first.isError)
            self.assertIn('repeat_count', second.content[0].text)
            final = json.loads(json.loads(third.content[0].text)['output'])
            self.assertFalse(final['full'])
            self.assertEqual(final['next_op'], 'begin')     # the cell stops; Vivado stays open
            self.assertTrue(final['cell_stopped'])
            self.assertTrue(state.saved)

    def test_structured_request_repairs_response_echoes(self):
        request = {'operation': 'begin', 'ip_name': 'uart', 'cell_name': 'u0',
                   'block_design': 'nl_bd', 'session_preserved': True, 'stage': 'finished'}
        repairs = guard.repair_response_echoes(request)
        self.assertEqual(request, {'op': 'begin', 'ip': 'uart', 'cell': 'u0'})
        self.assertEqual(len(repairs), 6)

    def test_matching_duplicate_structured_session_is_removed(self):
        request = {'op': 'finish', 'session_id': 'vivado-1'}
        self.assertTrue(guard.remove_redundant_session(request, 'vivado-1'))
        self.assertEqual(request, {'op': 'finish'})
        with self.assertRaisesRegex(ValueError, 'does not match'):
            guard.remove_redundant_session({'op': 'finish', 'session_id': 'other'}, 'vivado-1')



    async def test_preinit_failure_allows_valid_begin_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = '/scratch/project/.vivado-ai/log'
            from structured_config import StructuredSession
            state = StructuredSession(g, FakeUpstream(False), 's', g.started['s'])
            state.stage = 'finished'
            state.borrowed_session = True
            state.initialized = False
            state.terminal_stop = False
            state.final_reply = {'stage': 'finished', 'full': False, 'next_op': 'begin'}
            g.structured_sessions['s'] = state
            await g.execute(FakeUpstream(False), {'session_id': 's', 'command': json.dumps({
                'op': 'begin', 'ip': 'generic', 'cell': 'same_0', 'part': 'part',
                'requirements': ['width 8']})})
            self.assertIsNot(g.structured_sessions['s'], state)
            self.assertFalse(g.structured_sessions['s'].terminal_stop)

    async def test_begin_after_finish_starts_the_next_cell_in_the_same_session(self):
        """finish keeps Vivado open, so the next prompt's begin gets a fresh cell ledger on the
        same session -- for a session the skill started, not only a borrowed one -- including
        after another cell stopped on repeated failures."""
        from structured_config import StructuredSession
        for terminal in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                g = guard.ExecutionGuard(tmp)
                g.started['s'] = '/scratch/project/proj/.vivado-ai/log'
                state = StructuredSession(g, FakeUpstream(False), 's', g.started['s'])
                state.stage, state.initialized, state.borrowed_session = 'finished', True, False
                state.terminal_stop, state.cell = terminal, 'gpio_0'
                state.final_reply = {'stage': 'finished', 'next_op': 'begin'}
                g.structured_sessions['s'] = state
                await g.execute(FakeUpstream(False), {'session_id': 's', 'command': json.dumps({
                    'op': 'begin', 'ip': 'generic', 'cell': 'uart_0', 'part': 'part',
                    'requirements': ['width 8']})})
                self.assertIsNot(g.structured_sessions['s'], state)

    async def test_begin_after_vivado_was_stopped_does_not_restart(self):
        from structured_config import StructuredSession
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = '/scratch/project/proj/.vivado-ai/log'
            state = StructuredSession(g, FakeUpstream(False), 's', g.started['s'])
            state.stage, state.initialized = 'finished', True
            state.final_reply = {'stage': 'finished', 'next_op': 'stop'}
            g.structured_sessions['s'] = state
            g.closed.add('s')
            await g.execute(FakeUpstream(False), {'session_id': 's', 'command': json.dumps({
                'op': 'begin', 'ip': 'generic', 'cell': 'uart_0', 'part': 'part', 'requirements': ['width 8']})})
            self.assertIs(g.structured_sessions['s'], state)

    async def test_terminal_structured_failure_cannot_restart_borrowed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = guard.ExecutionGuard(tmp)
            g.started['s'] = '/scratch/project/.vivado-ai/log'
            from structured_config import StructuredSession
            state = StructuredSession(g, FakeUpstream(False), 's', g.started['s'])
            state.stage = 'finished'
            state.borrowed_session = True
            state.initialized = True
            state.terminal_stop = True
            state.cell = 'same_0'
            state.final_reply = {'stage': 'finished', 'full': False, 'next_op': 'stop'}
            g.structured_sessions['s'] = state
            result = await g.execute(FakeUpstream(False), {'session_id': 's', 'command': json.dumps({
                'op': 'begin', 'ip': 'generic', 'cell': 'same_0', 'part': 'part',
                'requirements': ['width 8']})})
            payload = json.loads(json.loads(result.content[0].text)['output'])
            self.assertEqual(payload['next_op'], 'stop')
            self.assertIs(g.structured_sessions['s'], state)

    async def test_completeness_is_data_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'should-not-exist'
            self.assertTrue(await guard.tcl_complete(f'set ch [open {{{marker}}} w];close $ch'))
            self.assertFalse(marker.exists())
            self.assertFalse(await guard.tcl_complete('set x {unfinished'))

    async def test_incomplete_script_is_not_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            upstream = FakeUpstream()
            result = await guard.ExecutionGuard(tmp).execute(upstream, {'session_id': 's', 'command': 'set x {unfinished'})
            self.assertTrue(result.isError)
            self.assertEqual(upstream.calls, [])

    async def test_duplicate_failure_refusal_and_partial_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, upstream = guard.ExecutionGuard(tmp), FakeUpstream()
            args = {'session_id': 's', 'command': 'bad_command'}
            await g.execute(upstream, args)
            await g.execute(upstream, args)
            result = await g.execute(upstream, args)
            self.assertIn('REPEATED_FAILURE', result.content[0].text)
            self.assertEqual(sum(a['command'] == 'bad_command' for _, a in upstream.calls), 2)
            self.assertIn('save_bd_design', upstream.calls[-1][1]['command'])

    async def test_repeated_incomplete_command_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, upstream = guard.ExecutionGuard(tmp), FakeUpstream()
            args = {'session_id': 's', 'command': 'set x {unfinished'}
            await g.execute(upstream, args)
            await g.execute(upstream, args)
            result = await g.execute(upstream, args)
            self.assertIn('REPEATED_FAILURE', result.content[0].text)
            self.assertEqual(len(upstream.calls), 1)
            self.assertIn('save_bd_design', upstream.calls[0][1]['command'])

    async def test_compact_output_retains_full_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = await guard.ExecutionGuard(tmp).execute(FakeUpstream(False, True), {'session_id': 's', 'command': 'puts data'})
            data = guard.payload(result)
            self.assertTrue(data['output_truncated'])
            self.assertLess(len(data['output']), 2000)
            self.assertIn('X' * 10000, Path(data['evidence_path']).read_text())


class RunRootTest(unittest.TestCase):
    """The run root must never climb out of the run, whatever Vivado's working directory is.

    begin puts proj/, evidence/ and cache/ under the root it derives from Vivado's
    session log. Deriving it by always taking the working directory's parent is only
    right when that directory is <root>/proj. Started anywhere else -- which happens
    when vivado_start refuses a working_dir that is not there and the agent falls
    back to the workspace root -- the root lands on the directory holding every
    case's workspace, and init reopens an existing project of the same part instead
    of refusing, so runs silently build on top of each other. This is the escape that
    failed ced-gen_E2 and ced-gen_M7 (PR #747); the same derivation lived here.
    """

    def test_proj_subfolder_gives_its_parent(self):
        self.assertEqual(run_root('/runs/case1/proj/.vivado-ai/s.log'), Path('/runs/case1'))

    def test_other_working_directory_is_its_own_root(self):
        self.assertEqual(run_root('/runs/case1/.vivado-ai/s.log'), Path('/runs/case1'))

    def test_root_stays_inside_the_working_directory_tree(self):
        for workdir in ('/runs/case1', '/runs/case1/proj', '/runs/case1/scratch/design'):
            root = run_root(workdir + '/.vivado-ai/s.log')
            self.assertTrue(str(workdir).startswith(str(root)),
                            '%s escaped to %s' % (workdir, root))
            self.assertNotEqual(root, Path('/runs'), '%s escaped to the shared parent' % workdir)

    def test_tcl_copies_agree_with_the_python_one(self):
        """lite::run_root and ipcfg_op::root must derive the same root as run_root."""
        out = run_tcl('''
file mkdir [file join $::env(TMPDIR) case1 proj]
puts [lite::run_root [file join $::env(TMPDIR) case1 proj]]
puts [lite::run_root [file join $::env(TMPDIR) case1]]
source {%s}
cd [file join $::env(TMPDIR) case1 proj];puts [ipcfg_op::root]
cd [file join $::env(TMPDIR) case1];puts [ipcfg_op::root]
''' % (SKILL / 'lib/ipcfg_op.tcl'))
        self.assertEqual(out.returncode, 0, out.stderr)
        # sourcing ipcfg_op.tcl prints its own load line first; the four roots follow.
        lines = [line for line in out.stdout.splitlines() if line.strip()][-4:]
        self.assertEqual(len(lines), 4, out.stdout)
        self.assertTrue(lines[0].endswith('/case1'), lines[0])
        for line in lines:
            self.assertEqual(line, lines[0], out.stdout)
        self.assertEqual(str(run_root(lines[0] + '/proj/.vivado-ai/s.log')), lines[0])

    def test_no_fixed_climb_remains(self):
        """The rule lives in four places; a copy that drifts reopens the escape."""
        for path, marker in (('lib/lite.tcl', 'proc lite::run_root'),
                             ('lib/ipcfg_op.tcl', 'proc ipcfg_op::root'),
                             ('scripts/structured_config.py', 'def run_root')):
            text = (SKILL / path).read_text()
            self.assertIn(marker, text, '%s lost its copy of the rule' % path)
            self.assertIn('proj', text.split(marker, 1)[1][:900],
                          '%s no longer guards on the proj working directory' % path)
        for path in ('scripts/structured_config.py', 'scripts/ipcfg_bridge.py'):
            self.assertNotIn('parents[2]', (SKILL / path).read_text(),
                             '%s: a fixed climb is back' % path)
        lite_tcl = (SKILL / 'lib/lite.tcl').read_text()
        for climb in ('set outdir [file dirname $workdir]',
                      'set expected [file normalize [file dirname $workdir]]'):
            self.assertNotIn(climb, lite_tcl, 'lib/lite.tcl: a fixed climb is back')


if __name__ == '__main__':
    unittest.main()
