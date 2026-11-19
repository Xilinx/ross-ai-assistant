# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The move to a driving cell names the parameter that sets the pin's frequency, or declines.

ced-gen_H3 on Gemma-31B: the refusal on clk_wiz_0 said ps_e drives clk_in1 "through
pl_clk0". The model moved to ps_e and described it as "pl_clk0 frequency",
"PL_CLK0 frequency", "PSU_PL_CLK0_FREQ" and "PL_CLK0_FREQ" -- the pin's name, four
times -- and never reached PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ, whose name never says
"clock". The design stayed at 100 MHz.

The fixture is real Vivado output: every frequency parameter of the driving cell whose
current value equals the pin's frequency. On ps_e that is 43 parameters, so the value
only narrows the field; on a Zynq-7000 it includes PCW_CLK0_FREQ, which matches on
value, VALUE_SRC and name alike and silently ignores a write. A wrong name would send
the model to write it, so declining is always preferred to guessing.
"""
import json
import os
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
LITE = Path(os.environ.get('CED_LITE_SKILL', SKILL / 'engine')).resolve()
sys.path[:0] = [str(SKILL / 'scripts'), str(LITE / 'scripts')]

from ced_session import frequency_setter  # noqa: E402

PINS = json.loads((SKILL / 'tests' / 'fixtures' / 'frequency_sources.json').read_text())['pins']


def setter(pin_path, requirement):
    return frequency_setter(pin_path.split('/')[1], PINS[pin_path]['matches'], requirement)


class FrequencySetterTests(unittest.TestCase):
    def test_h3_names_the_pl0_clock_on_an_ultrascale_ps(self):
        self.assertEqual(setter('ps_e/pl_clk0', 'Feed the clocking wizard from a 125 MHz PL clock 0'),
                         'PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ')

    def test_the_pin_number_settles_a_numbered_family(self):
        """PL0..PL3 all sit at 100 MHz on ps_e; pl_clk0 is the one the pin is."""
        self.assertEqual(setter('ps_e/pl_clk0', 'PL clock at 150 MHz'), 'PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ')
        self.assertEqual(setter('processing_system7_0/FCLK_CLK1', 'PL clock 1 at 150 MHz'),
                         'PCW_FPGA1_PERIPHERAL_FREQMHZ')

    def test_m7_names_fpga0_on_a_zynq_7000_and_never_its_decoy(self):
        for requirement in ('PL clock at 100 MHz', 'FPGA clock 0 at 100 MHz'):
            with self.subTest(requirement=requirement):
                self.assertEqual(setter('processing_system7_0/FCLK_CLK0', requirement), 'PCW_FPGA0_PERIPHERAL_FREQMHZ')

    def test_a_request_that_does_not_say_which_clock_is_declined_not_guessed(self):
        """Only clock/frequency words in common is what the decoy has too."""
        for pin, requirement in (('processing_system7_0/FCLK_CLK0', 'Clock the BRAM controller at 100 MHz'),
                                 ('ps_e/pl_clk0', 'fabric clock 0 at 150 MHz')):
            with self.subTest(requirement=requirement):
                self.assertIsNone(setter(pin, requirement))

    def test_a_word_that_only_extends_clock_does_not_count(self):
        """"clocking" once named a PCIe reference clock for a clocking-wizard request."""
        self.assertIsNone(setter('ps_e/pl_clk0', 'Clock the clocking wizard input at 150 MHz'))

    def test_a_readback_is_never_named(self):
        """Every PL0 setting on ps_e has an ACT_ twin reading the same frequency."""
        keys = [m['key'] for m in PINS['ps_e/pl_clk0']['matches']]
        self.assertIn('PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ', keys)
        self.assertNotIn('ACT', setter('ps_e/pl_clk0', 'actual PL clock 0 frequency'))

    def test_nothing_to_go_on_is_none(self):
        self.assertIsNone(frequency_setter('pl_clk0', [], 'PL clock at 100 MHz'))


if __name__ == '__main__':
    unittest.main()
