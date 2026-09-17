"""G.711 model derived from the Recommendation's tables (Table 1a A-law, Table 2a mu-law).

Written independently of STL g711.c so the two can be cross-checked.
Interface matches STL: 16-bit two's complement linear PCM <-> 8-bit character.
"""

A_SHIFT = 3  # 16-bit -> 13-bit A-law scale (+-4096)
U_SHIFT = 2  # 16-bit -> 14-bit mu-law scale (+-8159)


def alaw_encode(x16: int) -> int:
    x = x16 >> A_SHIFT
    if x >= 0:
        sign, mag = 0x80, x
    else:
        # Negative decision intervals mirror the positive ones with the
        # boundary belonging to the smaller magnitude (one's complement).
        sign, mag = 0x00, -x - 1
    mag = min(mag, 4095)
    if mag < 32:
        seg, step = 0, mag >> 1
    else:
        seg = mag.bit_length() - 5          # 32..63 -> 1, ..., 2048..4095 -> 7
        step = (mag >> seg) - 16
    return (sign | (seg << 4) | step) ^ 0x55  # even bits inverted


def alaw_decode(code: int) -> int:
    c = code ^ 0x55
    seg, step = (c >> 4) & 7, c & 0xF
    if seg == 0:
        recon = 2 * step + 1
    else:
        recon = (2 * (16 + step) + 1) << (seg - 1)
    recon <<= A_SHIFT
    return recon if c & 0x80 else -recon


def ulaw_encode(x16: int) -> int:
    x = x16 >> U_SHIFT
    if x >= 0:
        sign, mag = 0x80, x
    else:
        sign, mag = 0x00, -x - 1
    v = min(mag + 33, 8191)                 # biased magnitude, top of segment 8
    seg = v.bit_length() - 6                # 32..63 -> 0, ..., 4096..8191 -> 7
    step = ((v >> (seg + 1)) - 16) & 0xF
    # Magnitude bits inverted; transmitted sign bit is 1 for positive (+0 -> 0xFF).
    return sign | ((~((seg << 4) | step)) & 0x7F)


def ulaw_decode(code: int) -> int:
    c = code ^ 0x7F
    seg, step = (c >> 4) & 7, c & 0xF
    recon = ((2 * (16 + step) + 1) << seg) - 33
    recon <<= U_SHIFT
    return recon if c & 0x80 else -recon
