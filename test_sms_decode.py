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

from app import decode_hex_message, try_decode_hex, _build_multipart_pdus


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


class TestBuildMultipartPdus(unittest.TestCase):
    """Tests for _build_multipart_pdus() – the PDU builder used when sending
    long messages that would not fit inside a single SMS."""

    VODAFONE_MSG = (
        "Il tuo codice di verifica: 938707 "
        "Il tuo codice di verifica scade il: "
        "16 mar 2026, 05:19:41 "
        "Non passare mai il codice di verifica a qualcun altro. "
        "Gli operatori Vodafone non ti chiederanno mai questo codice."
    )

    # ------------------------------------------------------------------
    # Basic structure tests
    # ------------------------------------------------------------------
    def test_long_ascii_message_produces_multiple_parts(self):
        """A 207-char ASCII message must produce more than one part."""
        self.assertGreater(len(self.VODAFONE_MSG), 160)
        pdus = _build_multipart_pdus('+39123456789', self.VODAFONE_MSG, ref=0x42)
        self.assertGreater(len(pdus), 1)

    def test_returns_tuples_of_hex_and_length(self):
        pdus = _build_multipart_pdus('+39123456789', self.VODAFONE_MSG, ref=1)
        for pdu_hex, pdu_len in pdus:
            self.assertIsInstance(pdu_hex, str)
            self.assertIsInstance(pdu_len, int)
            # pdu_hex must be valid hex
            bytes.fromhex(pdu_hex)
            # pdu_len must equal the byte-length of the PDU minus the SMSC byte
            self.assertEqual(pdu_len, (len(pdu_hex) - 2) // 2)

    def test_pdu_starts_with_smsc_00(self):
        """PDU must start with '00' (use SIM default SMSC)."""
        pdus = _build_multipart_pdus('+1555000111', self.VODAFONE_MSG, ref=5)
        for pdu_hex, _ in pdus:
            self.assertTrue(pdu_hex.startswith('00'),
                            f"PDU does not start with SMSC 00: {pdu_hex[:10]}")

    # ------------------------------------------------------------------
    # UDH / sequence-number tests
    # ------------------------------------------------------------------
    def _ud_start(self, raw):
        """Return the byte offset of the UD field inside a raw PDU bytearray.

        Layout: SMSC(1) + FO(1) + MR(1) + AddrLen(1) + TonNpi(1) +
                BcdAddr(ceil(addr_digits/2)) + PID(1) + DCS(1) + VP(1) + UDL(1)
        """
        addr_bcd_bytes = (raw[3] + 1) // 2
        return (1 +              # SMSC '00'
                1 +              # first octet
                1 +              # MR
                1 +              # address length
                1 +              # TON/NPI
                addr_bcd_bytes +
                1 +              # PID
                1 +              # DCS
                1 +              # VP
                1)               # UDL

    def test_parts_have_correct_seq_and_total(self):
        """Every part must carry the correct UDH sequence information."""
        pdus = _build_multipart_pdus('+39123456789', self.VODAFONE_MSG, ref=0x42)
        total_parts = len(pdus)
        for expected_seq, (pdu_hex, _) in enumerate(pdus, start=1):
            raw = bytes.fromhex(pdu_hex)
            ud_start = self._ud_start(raw)
            udh = raw[ud_start:ud_start + 6]
            self.assertEqual(udh[0], 0x05, "UDHL should be 5")
            self.assertEqual(udh[1], 0x00, "IEI should be 0x00")
            self.assertEqual(udh[2], 0x03, "IEIL should be 3")
            # udh[3] = ref – just check it is non-zero (we passed 0x42)
            self.assertEqual(udh[3], 0x42, "ref should match")
            self.assertEqual(udh[4], total_parts, "total_parts in UDH")
            self.assertEqual(udh[5], expected_seq, "seq_num in UDH")

    def test_reassembled_text_matches_original(self):
        """Parts decoded with decode_hex_message and sorted by seq must
        reproduce the original message exactly."""
        pdus = _build_multipart_pdus('+39123456789', self.VODAFONE_MSG, ref=0x10)
        parts = []
        for idx, (pdu_hex, _) in enumerate(pdus, start=1):
            raw = bytes.fromhex(pdu_hex)
            ud_start = self._ud_start(raw)
            udl = raw[ud_start - 1]
            ud_hex = raw[ud_start:ud_start + udl].hex().upper()
            seq, total, decoded = decode_hex_message(ud_hex)
            self.assertGreater(seq, 0, f"Part {idx} has no seq number")
            parts.append({'id': str(idx), 'seq': seq, 'text': decoded})

        ordered = sorted(parts, key=lambda p: p['seq'])
        combined = ''.join(p['text'] for p in ordered)
        self.assertEqual(combined, self.VODAFONE_MSG)

    # ------------------------------------------------------------------
    # Reference number tests
    # ------------------------------------------------------------------
    def test_ref_is_embedded_in_all_parts(self):
        """The supplied ref byte must appear in the UDH of every part."""
        ref = 0xAB
        pdus = _build_multipart_pdus('+1555000111', self.VODAFONE_MSG, ref=ref)
        for pdu_hex, _ in pdus:
            raw = bytes.fromhex(pdu_hex)
            ud_start = self._ud_start(raw)
            udh = raw[ud_start:ud_start + 6]
            self.assertEqual(udh[3], ref & 0xFF)

    # ------------------------------------------------------------------
    # Two-part message: verify correct ordering even if delivered reversed
    # ------------------------------------------------------------------
    def test_two_part_message_ordering(self):
        text = 'A' * 200  # 200 chars > 160, fits in 3 UCS-2 parts (67+67+66)
        pdus = _build_multipart_pdus('+49170123456', text, ref=7)
        # Simulate reversed delivery
        parts = []
        for idx, (pdu_hex, _) in enumerate(pdus, start=1):
            raw = bytes.fromhex(pdu_hex)
            ud_start = self._ud_start(raw)
            udl = raw[ud_start - 1]
            ud_hex = raw[ud_start:ud_start + udl].hex().upper()
            seq, _, decoded = decode_hex_message(ud_hex)
            parts.append({'id': str(idx), 'seq': seq, 'text': decoded})

        # Reverse to simulate wrong arrival order
        parts_reversed = list(reversed(parts))
        ordered = sorted(parts_reversed, key=lambda p: p['seq'])
        combined = ''.join(p['text'] for p in ordered)
        self.assertEqual(combined, text)


if __name__ == '__main__':
    unittest.main()
