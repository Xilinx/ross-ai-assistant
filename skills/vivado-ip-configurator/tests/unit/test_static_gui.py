import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from static_gui import extract, update_dependencies, numeric_transforms


class StaticGuiTests(unittest.TestCase):
    def test_literal_update_inputs_expose_enum_controller_without_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gui.tcl'
            path.write_text('''proc update_PARAM_VALUE.INPUTS {PARAM_VALUE.MODE PARAM_VALUE.INPUTS} {
 set mode [get_property value ${PARAM_VALUE.MODE}]
 set_property enabled true ${PARAM_VALUE.INPUTS}
 if {$mode == 1} {set_property enabled false ${PARAM_VALUE.INPUTS}}
 elseif {$mode == 2} {set_property range "1,2,3,4" ${PARAM_VALUE.INPUTS}}
}''')
            rules = update_dependencies(path)
            self.assertEqual(rules['INPUTS']['controls'], ['MODE'])
            self.assertEqual(rules['INPUTS']['comparisons'], ['MODE == 1', 'MODE == 2'])

    def test_numeric_transform_requires_complete_literal_update(self):
        source = """proc update_PARAM_VALUE.OUT_HZ {PARAM_VALUE.OUT_HZ PARAM_VALUE.DISPLAY} {
 set input_value [get_property value ${PARAM_VALUE.DISPLAY}]
 set output_value [expr {int($input_value*1000)} ]
 set_property value $output_value ${PARAM_VALUE.OUT_HZ}
}"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gui.tcl';path.write_text(source)
            rule=numeric_transforms(path)['DISPLAY']
            self.assertEqual((rule['target'],rule['factor']),('CONFIG.OUT_HZ',1000))
            path.write_text(source+'\n'+source.replace('OUT_HZ','OTHER_HZ'))
            self.assertEqual(numeric_transforms(path),{})
            path.write_text(source.replace('*1000','*[exec dangerous]'))
            self.assertEqual(numeric_transforms(path),{})
            path.write_text(source.replace(' set output_value',' if {$condition} {return}\n set output_value'))
            self.assertEqual(numeric_transforms(path),{})

    def parse(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gui.tcl'
            path.write_text(text)
            return extract(path)

    def test_parent_context_and_literal_help(self):
        result = self.parse('''proc init_gui {IPINST} {
 set page [ipgui::add_page $IPINST -name {Configuration}]
 set group [ipgui::add_group $IPINST -name "Port B" -parent $page]
 set panel [ipgui::add_panel $IPINST -parent $group -name panel1]
 set param [ipgui::add_param $IPINST -name WIDTH -parent $panel]
 set_property tooltip {Number of bits} $param
}
proc update_value {VALUE} {
 set_property tooltip {Not a GUI declaration} $param
}
''')
        self.assertEqual(result, {'WIDTH': {'gui_context': ['Configuration', 'Port B'], 'help': 'Number of bits'}})

    def test_dynamic_source_is_not_evaluated(self):
        result = self.parse('''proc init_gui {IPINST} {
 set p [ipgui::add_param $IPINST -name WIDTH]
 set_property tooltip "[exec touch /tmp/ipcfg-should-not-exist]" $p
 set q [ipgui::add_param $IPINST -name $dynamic]
 set_property tooltip $description $p
}''')
        self.assertEqual(result, {'WIDTH': {'gui_context': []}})


if __name__ == '__main__':
    unittest.main()
