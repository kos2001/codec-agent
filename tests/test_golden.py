"""L0: lock down the oracle before anything is compared against it (adapted from hls-rtl tests/).

No simulator needed. The STL-backed checks skip — not pass — when the STL build is absent.
"""
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "golden" / "g711"))
import spec_model as m  # noqa: E402

TD = ROOT / "third_party/stl/src/g711/test_data"
STL_BIN = ROOT / "third_party/stl/build/bin/g711demo"
VEC = ROOT / "generated/g711/tb/vectors"
ALL16 = range(-32768, 32768)


def rd16(p: Path):
    d = p.read_bytes()
    return list(struct.unpack("<%dh" % (len(d) // 2), d))


# --- anchors from the Recommendation's tables (independent of any implementation)

@pytest.mark.parametrize("code,value", [
    (0xD5, 8), (0x55, -8),            # A-law smallest magnitude, seg 0 step 0
    (0xAA, 32256), (0x2A, -32256),    # A-law largest: (2*31+1) << 6, x8
])
def test_alaw_decode_anchors(code, value):
    assert m.alaw_decode(code) == value


@pytest.mark.parametrize("code,value", [
    (0xFF, 0), (0x7F, 0),             # mu-law +0 / -0
    (0x80, 32124), (0x00, -32124),    # mu-law largest: 8031 x4
])
def test_ulaw_decode_anchors(code, value):
    assert m.ulaw_decode(code) == value


def test_zero_encodes_to_positive_zero_codes():
    assert m.alaw_encode(0) == 0xD5
    assert m.ulaw_encode(0) == 0xFF


# --- structural properties of the companding law

@pytest.mark.parametrize("enc,dec", [(m.alaw_encode, m.alaw_decode), (m.ulaw_encode, m.ulaw_decode)])
def test_encode_is_monotonic_in_reconstruction(enc, dec):
    prev = None
    for x in ALL16:
        y = dec(enc(x))
        assert prev is None or y >= prev, f"non-monotonic at x={x}"
        prev = y


@pytest.mark.parametrize("dec", [m.alaw_decode, m.ulaw_decode])
def test_decode_is_odd_symmetric(dec):
    for c in range(128, 256):
        assert dec(c) == -dec(c & 0x7F)


@pytest.mark.parametrize("enc,dec", [(m.alaw_encode, m.alaw_decode), (m.ulaw_encode, m.ulaw_decode)])
def test_decode_then_encode_is_identity_on_codes(enc, dec):
    # Each code's reconstruction value lies inside its own decision interval.
    # mu-law has two zero codes (0xFF, 0x7F) that both reconstruct to 0 -> 0xFF.
    for c in range(256):
        expect = 0xFF if (enc is m.ulaw_encode and c == 0x7F) else c
        assert enc(dec(c)) == expect, hex(c)


@pytest.mark.parametrize("enc", [m.alaw_encode, m.ulaw_encode])
def test_encode_reaches_every_code(enc):
    # Includes mu-law negative zero 0x7F: inputs -4..-1 land there (agrees with STL).
    assert {enc(x) for x in ALL16} == set(range(256))
    if enc is m.ulaw_encode:
        assert [enc(x) for x in (-4, -1, 0, 3)] == [0x7F, 0x7F, 0xFF, 0xFF]


# --- the spec model agrees with ITU-T STL (the cross-check that found the mu-law sign bug)

needs_vectors = pytest.mark.skipif(not TD.exists(), reason="STL not cloned: missing evidence, not a pass")


@needs_vectors
@pytest.mark.parametrize("fn,ref", [(m.alaw_encode, "sweep-r.a"), (m.ulaw_encode, "sweep-r.u")])
def test_encoder_matches_stl_official_vectors(fn, ref):
    src, exp = rd16(TD / "sweep.src"), rd16(TD / ref)
    assert len(set(src)) == 65536  # the sweep really is exhaustive
    bad = [(x, fn(x), e) for x, e in zip(src, exp) if fn(x) != e]
    assert not bad, bad[:4]


@needs_vectors
@pytest.mark.parametrize("fn,ref,law", [(m.alaw_decode, "codes.alin", "A"), (m.ulaw_decode, "codes.ulin", "u")])
def test_decoder_matches_stl_on_all_codes(fn, ref, law, tmp_path):
    if not STL_BIN.exists():
        pytest.skip("STL g711demo not built: missing evidence, not a pass")
    codes = tmp_path / "codes.lo"
    codes.write_bytes(struct.pack("<256h", *range(256)))
    out = tmp_path / ref
    subprocess.run([STL_BIN, law, "loli", codes, out, "256", "1", "1"], check=True, capture_output=True)
    assert [fn(c) for c in range(256)] == rd16(out)


# --- the generated vector set is what the testbench claims it is

def test_tests_manifest_references_existing_vectors():
    if not (VEC / "tests.txt").exists():
        pytest.skip("vectors not generated")
    for line in (VEC / "tests.txt").read_text().splitlines():
        name, mode, n, bp, stem = line.split()
        stim = (VEC / f"{stem}.stim").read_text().split()
        exp = (VEC / f"{stem}.exp").read_text().split()
        assert len(stim) == len(exp) == int(n), name
        assert bp in ("0", "1") and 0 <= int(mode) <= 5
