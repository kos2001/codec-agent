"""Cross-check spec_model.py against STL outputs over the full input space."""
import struct, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import spec_model as m

ROOT = pathlib.Path(__file__).resolve().parents[2]
STL_BIN = ROOT / "third_party/stl/build/bin/g711demo"
TD = ROOT / "third_party/stl/src/g711/test_data"
OUT = pathlib.Path(__file__).parent / "stl_out"

def rd(p): d = p.read_bytes(); return list(struct.unpack("<%dh" % (len(d) // 2), d))

src = rd(TD / "sweep.src")
codes = list(range(256))
(OUT / "codes.lo").write_bytes(struct.pack("<256h", *codes))
for law, tag in (("A", "a"), ("u", "u")):
    subprocess.run([STL_BIN, law, "loli", OUT / "codes.lo", OUT / f"codes.{tag}lin", "256", "1", "1"],
                   check=True, capture_output=True)

fail = 0
for name, fn, xs, ref in (
    ("alaw_encode", m.alaw_encode, src, rd(TD / "sweep-r.a")),
    ("ulaw_encode", m.ulaw_encode, src, rd(TD / "sweep-r.u")),
    ("alaw_decode", m.alaw_decode, codes, rd(OUT / "codes.alin")),
    ("ulaw_decode", m.ulaw_decode, codes, rd(OUT / "codes.ulin")),
    ("alaw_roundtrip", lambda x: m.alaw_decode(m.alaw_encode(x)), src, rd(TD / "sweep-r.a-a")),
    ("ulaw_roundtrip", lambda x: m.ulaw_decode(m.ulaw_encode(x)), src, rd(TD / "sweep-r.u-u")),
):
    bad = [(x, fn(x), r) for x, r in zip(xs, ref) if fn(x) != r]
    print(f"{name:15s} {len(xs)-len(bad):6d}/{len(xs)} match", "" if not bad else f"first mismatches (in,model,stl): {bad[:4]}")
    fail += bool(bad)
sys.exit(fail)
