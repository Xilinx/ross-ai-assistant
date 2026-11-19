# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A cell whose customizer does not answer can still hold a value it produces.

On 2026.2 `::debug::dump_param_deps` fails for clk_wiz:6.0 -- its create_gui calls
add_param from an update method -- so every clk_wiz row comes back PB_MISS and the
cell has no parameter bank. The flow read that as "nothing can be written here" and
refused CONFIG.CLKOUT1_REQUESTED_OUT_FREQ, the one parameter that sets the output
frequency, telling the model to move the requirement to the cell that produces the
clock -- which was the clk_wiz it was already on. "MicroBlaze V clock at 200 MHz"
ended unresolved with the design at 100.

The refusal's premise is that nothing would check the value. That is true of a
value the cell receives, and false of one it drives: propose dry-runs the write,
runs validate_bd_design, reads the value back and restores Phase 1 on failure.
Written directly, 200 validates with 0 errors and reaches microblaze_riscv_0/Clk.

So the direction of the quantity, not the presence of the bank, decides.
"""
import os
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
LITE = Path(os.environ.get('CED_LITE_SKILL', SKILL / 'engine')).resolve()
sys.path[:0] = [str(SKILL / 'scripts'), str(LITE / 'scripts')]

from structured_config import StructuredSession  # noqa: E402


def row(key, **extra):
    """A describe row as it arrives from a cell with no bank: PB_MISS and live values."""
    return dict(dict(key='CONFIG.' + key, label=key.replace('_', ' ').title(), current='100.000',
                     range='PB_MISS:' + key, range_scope='unavailable'), **extra)


def session(*rows):
    s = StructuredSession(None, None, None, None)
    s.candidates = {'c%d' % i: r for i, r in enumerate(rows)}
    return s


class ReceivedQuantityTests(unittest.TestCase):
    def test_an_output_frequency_is_produced_by_the_cell_and_stays_writable(self):
        for key in ('CLKOUT1_REQUESTED_OUT_FREQ', 'CLKOUT4_REQUESTED_OUT_FREQ', 'CLK_OUT1_PORT',
                    'CLKOUT1_REQUESTED_OUT_MHZ', 'CLKOUT2_REQUESTED_OUT_PERIOD'):
            with self.subTest(key=key):
                self.assertFalse(StructuredSession.received_quantity(row(key)))

    def test_an_input_frequency_is_received_and_is_still_refused(self):
        for key in ('PRIM_IN_FREQ', 'REF_CLK_FREQ', 'CLKIN1_PERIOD', 'CLKIN2_JITTER_PS', 'C_CLK_FREQ_HZ'):
            with self.subTest(key=key):
                self.assertTrue(StructuredSession.received_quantity(row(key)),
                                key + ' is set on the cell that drives this one')

    def test_a_propagated_value_is_received_whatever_the_key_is_called(self):
        """value_src decides first: Vivado already says the number came from elsewhere."""
        self.assertTrue(StructuredSession.received_quantity(row('CLKOUT1_REQUESTED_OUT_FREQ', value_src='PROPAGATED')))
        self.assertTrue(StructuredSession.received_quantity(row('CLKOUT1_REQUESTED_OUT_FREQ', value_src='CONSTANT')))

    def test_a_row_about_nothing_clocklike_is_not_treated_as_received(self):
        """The refusal is about clocks and widths arriving from another cell, not about every row."""
        self.assertFalse(StructuredSession.received_quantity(row('C_GPIO_WIDTH')))
        self.assertFalse(StructuredSession.received_quantity(row('C_BAUDRATE')))


class BankBlocksWritesTests(unittest.TestCase):
    def test_a_clk_wiz_with_no_bank_is_not_blocked_because_it_owns_its_output_frequency(self):
        s = session(row('CLKOUT1_REQUESTED_OUT_FREQ'), row('PRIM_IN_FREQ'), row('CLKOUT1_USED'))
        self.assertTrue(s.bank_missing(), 'every row is PB_MISS, so the bank really is absent')
        self.assertFalse(s.bank_blocks_writes(), 'the output frequency is still settable here')

    def test_a_cell_holding_only_values_it_receives_is_blocked(self):
        s = session(row('REF_CLK_FREQ'), row('PRIM_IN_FREQ'))
        self.assertTrue(s.bank_missing())
        self.assertTrue(s.bank_blocks_writes())

    def test_a_cell_with_a_bank_is_never_blocked(self):
        s = session(dict(key='CONFIG.REF_CLK_FREQ', label='Ref clk freq', range='100.000 200.000',
                         range_scope='live'))
        self.assertFalse(s.bank_missing())
        self.assertFalse(s.bank_blocks_writes())

    def test_a_cell_described_by_nobody_yet_blocks_nothing(self):
        """Before the first describe there are no rows, so there is nothing to route away from."""
        s = session()
        self.assertFalse(s.bank_missing())
        self.assertFalse(s.bank_blocks_writes())

    def test_subkeys_do_not_decide_it(self):
        """Vector subkey rows are parts of a parent row and are not counted on their own."""
        s = session(row('CLKOUT1_REQUESTED_OUT_FREQ'), row('REF_CLK_FREQ', subkey='1'))
        self.assertFalse(s.bank_blocks_writes())


if __name__ == '__main__':
    unittest.main()
