#!/usr/bin/env bash
# Run all G.711 RTL tests listed in vectors/tests.txt with iverilog.
set -u
cd "$(dirname "$0")"
RTL=${RTL_DIR:-../rtl}/*.sv
BUILD=${BUILD:-build}; mkdir -p "$BUILD"
fail=0
while read -r name mode n bp stem; do
  iverilog -g2012 -o "$BUILD/$name.vvp" -s tb_g711 \
    -P tb_g711.MODE="$mode" -P tb_g711.N="$n" -P tb_g711.BP="$bp" \
    -P tb_g711.STIM="\"vectors/$stem.stim\"" -P tb_g711.EXP="\"vectors/$stem.exp\"" \
    tb_g711.sv $RTL 2> >(grep -v "sorry: constant selects" >&2) || { echo "COMPILE FAIL $name"; fail=1; continue; }
  vvp -n "$BUILD/$name.vvp" > "$BUILD/$name.log"
  result=$(grep -m1 '^RESULT' "$BUILD/$name.log" || echo "RESULT FAIL no-result")
  first_err=$(grep -m1 '^ERROR' "$BUILD/$name.log" || true)
  printf '%-20s %s %s\n' "$name" "$result" "${first_err:+| $first_err}"
  [[ $result == "RESULT PASS"* ]] || fail=1
done < vectors/tests.txt
exit $fail
