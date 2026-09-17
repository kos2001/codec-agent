"""L0: the oracle against the G.711 PDF itself (specs/g711/T-REC-G.711-198811-I-PDF-E.pdf).

Tables 1a/1b/2a/2b are rebuilt here from nothing but what the tables print:
column 2 (number of intervals x interval size), column 3 (segment end points),
Note 2 (character signal) and Note 3 (decoder output). No formula from
spec_model.py or STL is reused, so agreement is independent evidence.

What G.711 does NOT fix, and these tests therefore do not assert:
§3.6 — "The conversion to A-law or mu-law values from uniform PCM values
corresponding to the decision values, is left to the individual equipment
specification." Inputs exactly on a decision value are an implementation choice
(this design follows STL: REQ-A-02 / REQ-U-02). The encoder checks use interior
points only.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "golden" / "g711"))
import spec_model as m  # noqa: E402

PDF = ROOT / "specs/g711/T-REC-G.711-198811-I-PDF-E.pdf"
PDF_SHA256 = "4348b527fbb1488677503d3b0f65094fb8a4b01e159ce01d7adbc29d5efdc556"


def _decision_values(start, segments):
    """Column 5 from columns 2 and 3: walk each segment's intervals."""
    x = [start]
    for count, size in segments:
        for _ in range(count):
            x.append(x[-1] + size)
    return x


# Table 1a, column 2 (bottom to top) — segment 1 is "32 x 2", then 16 x 4 ... 16 x 128.
ALAW_X = _decision_values(0, [(32, 2), (16, 4), (16, 8), (16, 16), (16, 32), (16, 64), (16, 128)])
# Table 2a, column 2 — segment 1 is "1 x 1" then "15 x 2", then 16 x 4 ... 16 x 256.
ULAW_X = _decision_values(0, [(1, 1), (15, 2), (16, 4), (16, 8), (16, 16), (16, 32), (16, 64), (16, 128), (16, 256)])

A_SCALE = 8   # 4096 normalized units (13-bit) -> 16-bit linear
U_SCALE = 4   # 8159 normalized units (14-bit) -> 16-bit linear


def test_tables_rebuild_to_the_printed_values():
    # Column 3 segment end points and column 5 spot values, as printed.
    assert len(ALAW_X) == 129 and ALAW_X[128] == 4096
    assert [ALAW_X[n] for n in (1, 32, 33, 48, 64, 65, 80, 96, 97, 112, 113, 127)] == \
           [2, 64, 68, 128, 256, 272, 512, 1024, 1088, 2048, 2176, 3968]
    assert len(ULAW_X) == 129 and ULAW_X[128] == 8159
    assert [ULAW_X[n] for n in (1, 2, 16, 17, 32, 33, 48, 49, 64, 65, 80, 81, 96, 97, 112, 113, 126, 127)] == \
           [1, 3, 31, 35, 95, 103, 223, 239, 479, 511, 991, 1055, 2015, 2143, 4063, 4319, 7647, 7903]


def _alaw_rows():
    """(interval n, character before even-bit inversion, quantized value) per Table 1a/1b.
    Note 2: positive -> 128 + n, negative -> n.  Note 3: y = (x_{n} + x_{n+1}) / 2."""
    for n in range(128):
        y = (ALAW_X[n] + ALAW_X[n + 1]) // 2
        yield n, 128 + n, y
        yield n, n, -y


def _ulaw_rows():
    """Table 2a/2b. Note 2: positive -> 255 - n, negative -> 127 - n (no further inversion).
    Note 3: y_0 = 0, y_n = (x_n + x_{n+1}) / 2."""
    for n in range(128):
        y = 0 if n == 0 else (ULAW_X[n] + ULAW_X[n + 1]) // 2
        yield n, 255 - n, y
        yield n, 127 - n, -y


def test_printed_quantized_values():
    ya = {c: y for _, c, y in _alaw_rows()}
    yu = {c: y for _, c, y in _ulaw_rows()}
    # Table 1a/1b column 7 spot values (character before inversion -> y)
    assert [ya[c] for c in (0b10000000, 0b10100000, 0b10110000, 0b11000000, 0b11010000,
                            0b11100000, 0b11110000, 0b11111111, 0b00000000, 0b01111111)] == \
           [1, 66, 132, 264, 528, 1056, 2112, 4032, -1, -4032]
    # Table 2a/2b column 7
    assert [yu[c] for c in (0b11111111, 0b11111110, 0b11101111, 0b11011111, 0b11001111, 0b10111111,
                            0b10101111, 0b10011111, 0b10001111, 0b10000000, 0b01111111, 0b01111110,
                            0b00000001, 0b00000000)] == \
           [0, 2, 33, 99, 231, 495, 1023, 2079, 4191, 8031, 0, -2, -7775, -8031]


def test_alaw_decoder_matches_table_for_all_256_codes():
    for _, before, y in _alaw_rows():
        line = before ^ 0x55                  # Note 2: even bits (2,4,6,8) inverted on the line
        assert m.alaw_decode(line) == y * A_SCALE, f"code {line:#04x}"


def test_ulaw_decoder_matches_table_for_all_256_codes():
    for _, code, y in _ulaw_rows():
        assert m.ulaw_decode(code) == y * U_SCALE, f"code {code:#04x}"


def _interior(lo, hi):
    """Normalized values strictly inside (lo, hi); none when the interval has width 1."""
    return [v for v in {lo + 1, (lo + hi) // 2, hi - 1} if lo < v < hi]


def test_alaw_encoder_matches_table_on_interior_points():
    checked = 0
    for n, before, y in _alaw_rows():
        lo, hi = ALAW_X[n], ALAW_X[n + 1]
        for v in _interior(lo, hi):
            x16 = v * A_SCALE if y > 0 else -v * A_SCALE
            assert m.alaw_encode(x16) == before ^ 0x55, f"x={x16} (normalized {v}, n={n})"
            checked += 1
    assert checked > 400


def test_ulaw_encoder_matches_table_on_interior_points():
    checked = 0
    for n, code, y in _ulaw_rows():
        lo, hi = ULAW_X[n], ULAW_X[n + 1]
        negative = code < 128
        for v in _interior(lo, hi):
            x16 = -v * U_SCALE if negative else v * U_SCALE
            assert m.ulaw_encode(x16) == code, f"x={x16} (normalized {v}, n={n})"
            checked += 1
    assert checked > 400


def test_boundary_convention_is_documented_not_asserted_by_spec():
    """Inputs on a decision value: the spec is silent (§3.6), this design follows STL.
    Pin the adopted convention so a change to it is a visible, deliberate edit."""
    assert m.alaw_encode(2 * A_SCALE) == (128 + 1) ^ 0x55     # +x_1 -> upper interval
    assert m.alaw_encode(-2 * A_SCALE) == 0 ^ 0x55            # -x_1 -> smaller magnitude
    assert m.ulaw_encode(0) == 0xFF and m.ulaw_encode(-1 * U_SCALE) == 0x7F


def test_pdf_is_the_one_the_tests_were_written_against():
    if not PDF.exists():
        pytest.skip("spec PDF absent: missing evidence, not a pass")
    import hashlib
    assert hashlib.sha256(PDF.read_bytes()).hexdigest() == PDF_SHA256


def test_requirements_yaml_parses():
    """requirements.yaml is the pipeline's contract; it had an unquoted colon that made it
    unparseable while every human reader (and every other test) was happy with it."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((ROOT / "generated/g711/requirements.yaml").read_text())
    assert sorted(doc["alaw"]) == [f"REQ-A-0{i}" for i in range(1, 9)]
    assert sorted(doc["ulaw"]) == [f"REQ-U-0{i}" for i in range(1, 9)]
