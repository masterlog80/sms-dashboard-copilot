"""Unit tests for SMS hex-decoding and multi-part reassembly helpers."""

import sys
import os
import unittest

# Allow importing app without a running Flask/serial environment
sys.path.insert(0, os.path.dirname(__file__))

# Stub heavy imports so app.py can be imported without hardware
import types
import unittest.mock as mock

def _make_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_make_module('serial')
_make_module('smtplib')

# requests and sub-modules
_requests = _make_module('requests', get=mock.MagicMock(), post=mock.MagicMock())
_make_module('requests.auth', HTTPBasicAuth=mock.MagicMock())

# flask
_flask = _make_module(
    'flask',
    Flask=mock.MagicMock(return_value=mock.MagicMock()),
    render_template=mock.MagicMock(),
    request=mock.MagicMock(),
    jsonify=mock.MagicMock(),
    send_from_directory=mock.MagicMock(),
)

# Prevent the module-level `os.makedirs('/app/data', ...)` from failing in CI
import os as _os
_orig_makedirs = _os.makedirs
def _safe_makedirs(path, *args, **kwargs):
    if path.startswith('/app'):
        return
    return _orig_makedirs(path, *args, **kwargs)
_os.makedirs = _safe_makedirs

from app import decode_hex_message, try_decode_hex


def _make_ucs2_part(text, seq, total, ref=0x42, use_16bit_ref=False):
    """Build the hex string a GSM modem would emit for one UCS-2 SMS part."""
    if use_16bit_ref:
        # UDHL=6, IEI=0x08, IEIL=4, ref_hi, ref_lo, total, seq
        udh = bytes([0x06, 0x08, 0x04, (ref >> 8) & 0xFF, ref & 0xFF, total, seq])
    else:
        # UDHL=5, IEI=0x00, IEIL=3, ref, total, seq
        udh = bytes([0x05, 0x00, 0x03, ref & 0xFF, total, seq])
    payload = text.encode('utf-16-be')
    return (udh + payload).hex().upper()


class TestDecodeHexMessage(unittest.TestCase):
    """Tests for decode_hex_message()."""

    # ------------------------------------------------------------------
    # Plain-text (non-hex) pass-through
    # ------------------------------------------------------------------
    def test_plain_ascii_passthrough(self):
        text = "Hello, world!"
        seq, total, decoded = decode_hex_message(text)
        self.assertEqual(seq, 0)
        self.assertEqual(total, 0)
        self.assertEqual(decoded, text)

    def test_plain_italian_passthrough(self):
        text = "Ciao, come stai?"
        seq, total, decoded = decode_hex_message(text)
        self.assertEqual(decoded, text)

    # ------------------------------------------------------------------
    # Single-part UCS-2 (no UDH)
    # ------------------------------------------------------------------
    def test_single_part_ucs2_no_udh(self):
        original = "Hello"
        hex_str = original.encode('utf-16-be').hex().upper()
        seq, total, decoded = decode_hex_message(hex_str)
        self.assertEqual(seq, 0)
        self.assertEqual(total, 0)
        self.assertEqual(decoded, original)

    # ------------------------------------------------------------------
    # Multi-part UCS-2 with 8-bit UDH reference
    # ------------------------------------------------------------------
    def test_8bit_udh_part1(self):
        text = "First part of the message"
        hex_str = _make_ucs2_part(text, seq=1, total=2, ref=0x01)
        seq, total, decoded = decode_hex_message(hex_str)
        self.assertEqual(seq, 1)
        self.assertEqual(total, 2)
        self.assertEqual(decoded, text)

    def test_8bit_udh_part2(self):
        text = "Second part of the message"
        hex_str = _make_ucs2_part(text, seq=2, total=2, ref=0x01)
        seq, total, decoded = decode_hex_message(hex_str)
        self.assertEqual(seq, 2)
        self.assertEqual(total, 2)
        self.assertEqual(decoded, text)

    # ------------------------------------------------------------------
    # Multi-part UCS-2 with 16-bit UDH reference
    # ------------------------------------------------------------------
    def test_16bit_udh_part1(self):
        text = "Part one"
        hex_str = _make_ucs2_part(text, seq=1, total=3, ref=0x0100, use_16bit_ref=True)
        seq, total, decoded = decode_hex_message(hex_str)
        self.assertEqual(seq, 1)
        self.assertEqual(total, 3)
        self.assertEqual(decoded, text)

    def test_16bit_udh_part3(self):
        text = "Part three"
        hex_str = _make_ucs2_part(text, seq=3, total=3, ref=0x0100, use_16bit_ref=True)
        seq, total, decoded = decode_hex_message(hex_str)
        self.assertEqual(seq, 3)
        self.assertEqual(total, 3)
        self.assertEqual(decoded, text)

    # ------------------------------------------------------------------
    # The exact message from the bug report (4 UCS-2 parts at 67 chars each)
    # ------------------------------------------------------------------
    def _vodafone_parts(self):
        full = (
            "Il tuo codice di verifica: 153458 Il tuo codice di verifica scade il: "
            "16 mar 2026, 04:38:30 Non passare mai il codice di verifica a qualcun "
            "altro. Gli operatori Vodafone non ti chiederanno mai questo codice."
        )
        return [full[i:i + 67] for i in range(0, len(full), 67)]

    def test_vodafone_parts_decoded_correctly(self):
        parts_text = self._vodafone_parts()
        for idx, text in enumerate(parts_text, start=1):
            hex_str = _make_ucs2_part(text, seq=idx, total=len(parts_text))
            seq, total, decoded = decode_hex_message(hex_str)
            self.assertEqual(seq, idx, f"part {idx}: wrong seq")
            self.assertEqual(decoded, text, f"part {idx}: wrong text")


class TestTryDecodeHex(unittest.TestCase):
    """try_decode_hex must still work as before (returns only text)."""

    def test_plain_text(self):
        self.assertEqual(try_decode_hex("Hello"), "Hello")

    def test_ucs2_no_udh(self):
        hex_str = "Hello".encode('utf-16-be').hex().upper()
        self.assertEqual(try_decode_hex(hex_str), "Hello")

    def test_ucs2_with_udh_no_garbage(self):
        """UDH bytes must be stripped so decoded text is clean."""
        hex_str = _make_ucs2_part("Clean text", seq=1, total=2)
        result = try_decode_hex(hex_str)
        self.assertEqual(result, "Clean text")


class TestMultiPartReassembly(unittest.TestCase):
    """Verify that sorting by seq_num produces the correct combined message."""

    FULL_MESSAGE = (
        "Il tuo codice di verifica: 153458 Il tuo codice di verifica scade il: "
        "16 mar 2026, 04:38:30 Non passare mai il codice di verifica a qualcun "
        "altro. Gli operatori Vodafone non ti chiederanno mai questo codice."
    )

    def _build_parts(self, order):
        """Return a parts_list in the given modem-storage *order*.

        order is a list of 1-based sequence numbers, e.g. [1, 2, 4, 3]
        meaning modem stored part-4 at index 3 and part-3 at index 4.
        """
        text_chunks = [self.FULL_MESSAGE[i:i + 67] for i in range(0, len(self.FULL_MESSAGE), 67)]
        # assign modem storage ids by position in *order*
        parts = []
        for modem_idx, seq in enumerate(order, start=1):
            text = text_chunks[seq - 1]
            hex_str = _make_ucs2_part(text, seq=seq, total=len(text_chunks))
            s, _t, decoded = decode_hex_message(hex_str)
            parts.append({'id': str(modem_idx), 'seq': s, 'text': decoded})
        return parts

    def _combine(self, parts_list):
        has_seq = any(p['seq'] > 0 for p in parts_list)
        if has_seq:
            ordered = sorted(parts_list, key=lambda p: p['seq'])
        else:
            ordered = sorted(parts_list, key=lambda p: int(p['id']))
        return ''.join(p['text'] for p in ordered)

    def test_correct_order(self):
        parts = self._build_parts([1, 2, 3, 4])
        self.assertEqual(self._combine(parts), self.FULL_MESSAGE)

    def test_last_two_parts_swapped(self):
        """Reproduces the exact bug: part-4 stored before part-3."""
        parts = self._build_parts([1, 2, 4, 3])
        self.assertEqual(self._combine(parts), self.FULL_MESSAGE)

    def test_completely_reversed(self):
        parts = self._build_parts([4, 3, 2, 1])
        self.assertEqual(self._combine(parts), self.FULL_MESSAGE)

    def test_two_part_message_wrong_order(self):
        text = "Hello " * 30  # 180 chars, split into 2 parts: 67 + 113
        chunks = [text[:67], text[67:]]
        parts = []
        for modem_idx, (seq, chunk) in enumerate([(2, chunks[1]), (1, chunks[0])], start=1):
            hex_str = _make_ucs2_part(chunk, seq=seq, total=2)
            s, _t, decoded = decode_hex_message(hex_str)
            parts.append({'id': str(modem_idx), 'seq': s, 'text': decoded})
        combined = self._combine(parts)
        self.assertEqual(combined, text)


if __name__ == '__main__':
    unittest.main()
