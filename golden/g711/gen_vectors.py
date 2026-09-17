"""Generate hex stimulus/expected files for the RTL testbench (L1, L2, L3)."""
import struct, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import spec_model as m

ROOT = pathlib.Path(__file__).resolve().parents[2]
TD = ROOT / "third_party/stl/src/g711/test_data"
STL = pathlib.Path(__file__).parent / "stl_out"
VEC = ROOT / "generated/g711/tb/vectors"
VEC.mkdir(parents=True, exist_ok=True)

def rd(p): d = p.read_bytes(); return list(struct.unpack("<%dh" % (len(d) // 2), d))
def wr(name, vals): (VEC / name).write_text("".join(f"{v & 0xFFFF:04x}\n" for v in vals))

src, codes = rd(TD / "sweep.src"), list(range(256))
tests = {  # name: (mode, stimulus, expected)   mode: 0 aenc 1 adec 2 uenc 3 udec 4 art 5 urt
    "L1_alaw_enc":  (0, src, rd(TD / "sweep-r.a")),
    "L1_ulaw_enc":  (2, src, rd(TD / "sweep-r.u")),
    "L1_alaw_dec":  (1, rd(TD / "sweep-r.a"), rd(TD / "sweep-r.rea")),
    "L1_ulaw_dec":  (3, rd(TD / "sweep-r.u"), rd(TD / "sweep-r.reu")),
    "L1_alaw_rt":   (4, src, rd(TD / "sweep-r.a-a")),
    "L1_ulaw_rt":   (5, src, rd(TD / "sweep-r.u-u")),
    "L2_alaw_dec_all": (1, codes, rd(STL / "codes.alin")),
    "L2_ulaw_dec_all": (3, codes, rd(STL / "codes.ulin")),
}

# L3: spec-table directed vectors (segment edges +-1 in native scale, saturation, zero).
def edges(shift, bounds, full):
    xs = set()
    for b in bounds:
        for n in (b - 1, b, b + 1):
            for s in (n, -n, -n - 1):
                for lsb in (0, (1 << shift) - 1):
                    v = (s << shift) + lsb
                    if -32768 <= v <= 32767: xs.add(v)
    return sorted(xs | {-32768, 32767, 0, -1})
a_b = [0, 32] + [16 << s for s in range(1, 8)] + [4095, 4096]
u_b = [b - 33 for b in [32 << s for s in range(8)]] + [8158, 8159, 8160]
a_x, u_x = edges(3, a_b, 4096), edges(2, u_b, 8192)
tests["L3_alaw_enc_edges"] = (0, a_x, [m.alaw_encode(x) for x in a_x])
tests["L3_ulaw_enc_edges"] = (2, u_x, [m.ulaw_encode(x) for x in u_x])

# P: full-rate throughput guard (BP=0) per DUT and chain, reusing the vectors above.
full_rate = {"P_alaw_enc_fullrate": "L3_alaw_enc_edges", "P_ulaw_enc_fullrate": "L3_ulaw_enc_edges",
             "P_alaw_dec_fullrate": "L2_alaw_dec_all", "P_ulaw_dec_fullrate": "L2_ulaw_dec_all",
             "P_alaw_rt_fullrate": "L1_alaw_rt", "P_ulaw_rt_fullrate": "L1_ulaw_rt"}

lines = []  # name mode n bp vector_stem
for name, (mode, stim, exp) in tests.items():
    assert len(stim) == len(exp), name
    wr(f"{name}.stim", stim); wr(f"{name}.exp", exp)
    lines.append(f"{name} {mode} {len(stim)} 1 {name}")
for name, stem in full_rate.items():
    mode, stim, _ = tests[stem]
    lines.append(f"{name} {mode} {len(stim)} 0 {stem}")
(VEC / "tests.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
