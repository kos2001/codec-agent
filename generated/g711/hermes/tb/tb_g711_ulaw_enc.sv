module tb_g711_ulaw_enc;

  localparam int N_SWEEP  = 65536;
  localparam int N_EXTRA  = 16;
  localparam int N_STIM   = N_SWEEP + N_EXTRA;
  localparam int WD_LIMIT = 800000;

  logic clk;
  logic rst_n;
  logic in_valid;
  logic in_ready;
  logic signed [15:0] in_pcm;
  logic out_valid;
  logic out_ready;
  logic [7:0] out_code;

  logic [15:0] stim_q [$];
  logic [7:0]  exp_q  [$];

  int checks, errors, accepted, delivered;
  int i, r, gap, stall, idle_cnt, wd_cnt, rng_seed;
  logic offer;
  logic [7:0]  got, expct, got_cnt, exp_cnt;
  logic [15:0] stim_v;
  logic prev_ov, prev_or;
  logic [7:0]  prev_code;
  logic [15:0] prev_stim;

  g711_ulaw_enc dut (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(in_valid),
    .in_ready(in_ready),
    .in_pcm(in_pcm),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_code(out_code)
  );

  always #5 clk = ~clk;

  // ------------------------------------------------------------------
  // Reference encoder
  //   REQ-U-01  x14 = x16 >>> 2
  //   REQ-U-02  mag = x14 (x14>=0) ; mag = -x14-1 (x14<0)
  //   REQ-U-03  v = mag+33, saturate 8191
  //   REQ-U-04  v in [32*2^s, 64*2^s-1], step = (v-32*2^s) >> (s+1)
  //   REQ-U-05  bit7 = sign, 1 = positive
  //   REQ-U-06  bits6..0 = ~{segment, step}  => positive zero -> 0xFF
  // ------------------------------------------------------------------
  function automatic logic [7:0] ref_enc(input logic signed [15:0] x16);
    logic signed [15:0] x14;
    logic sg;
    int mag, v, s, st, m;
    begin
      x14 = x16 >>> 2;
      mag = x14;
      sg  = (x14 >= 0) ? 1'b1 : 1'b0;
      if (x14 < 0) mag = -mag - 1;

      v = mag + 33;
      if (v > 8191) v = 8191;

      if      (v >= 4096) s = 7;
      else if (v >= 2048) s = 6;
      else if (v >= 1024) s = 5;
      else if (v >=  512) s = 4;
      else if (v >=  256) s = 3;
      else if (v >=  128) s = 2;
      else if (v >=   64) s = 1;
      else                s = 0;

      st = (v - (32 << s)) >> (s + 1);
      m  = (s << 4) | st;
      ref_enc = (sg ? 8'h80 : 8'h00) | (8'h7F & ~m);
    end
  endfunction

  // Stimulus schedule: exhaustive -32768..32767 then boundary/zero/sign extras.
  function automatic logic signed [15:0] stim_at(input int n);
    begin
      if (n < N_SWEEP) begin
        stim_at = n - 32768;
      end else begin
        case (n - N_SWEEP)
          0:  stim_at = 16'sh0000;  // positive zero -> 0xFF
          1:  stim_at = 16'shFFFF;  // -1 -> negative zero 0x7F
          2:  stim_at = 16'shFFFE;  // -2
          3:  stim_at = 16'shFFFD;  // -3
          4:  stim_at = 16'shFFFC;  // -4
          5:  stim_at = 16'sh0001;
          6:  stim_at = 16'sh0002;
          7:  stim_at = 16'sh0003;
          8:  stim_at = 16'sh0004;
          9:  stim_at = 16'sh7FFF;  // +max
          10: stim_at = 16'sh8000;  // -max (saturation)
          11: stim_at = 16'sh007C;  // segment 0/1 boundary
          12: stim_at = 16'sh0078;  // top of segment 0
          13: stim_at = 16'sh3F7C;  // segment 6/7 boundary
          14: stim_at = 16'sh3FFC;
          15: stim_at = 16'sh7FFC;  // top step of segment 7
          default: stim_at = 16'sh0000;
        endcase
      end
    end
  endfunction

  // ------------------------------------------------------------------
  // Watchdog
  // ------------------------------------------------------------------
  always @(posedge clk) begin
    if (rst_n !== 1'b1) wd_cnt <= 0;
    else                wd_cnt <= wd_cnt + 1;
    if (wd_cnt >= WD_LIMIT) begin
      $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
      $finish;
    end
  end

  // ------------------------------------------------------------------
  // Stimulus + checker
  // ------------------------------------------------------------------
  initial begin
    clk       = 1'b0;
    rst_n     = 1'b0;
    in_valid  = 1'b0;
    in_pcm    = 16'sd0;
    out_ready = 1'b0;

    checks = 0; errors = 0; accepted = 0; delivered = 0;
    i = 0; r = 0; gap = 0; stall = 0; idle_cnt = 0;
    offer = 1'b0;
    prev_ov = 1'b0; prev_or = 1'b0; prev_code = 8'h00; prev_stim = 16'h0000;

    // Fixed seed: $urandom's seed argument must be an integer variable/register.
    rng_seed = 32'h5EED1234;
    rng_seed = $urandom(rng_seed);

    // REQ-IF-04: out_valid must be 0 while reset is asserted (synchronous).
    // Sample AFTER the edge has settled, otherwise the very first read returns
    // the DUT's pre-reset X value rather than the reset state.
    for (r = 0; r < 6; r = r + 1) begin
      @(posedge clk);
      #1;
      checks = checks + 1;
      if (out_valid !== 1'b0) begin
        errors = errors + 1;
        $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {7'b0000000, out_valid}, 8'h00);
      end
    end
    @(posedge clk);
    rst_n <= 1'b1;

    // out_valid still 0 on the first cycle after reset release
    @(posedge clk);
    #1;
    checks = checks + 1;
    if (out_valid !== 1'b0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {7'b0000000, out_valid}, 8'h00);
    end
    prev_ov = out_valid; prev_or = out_ready; prev_code = out_code; prev_stim = 16'h0000;

    while (1) begin
      @(posedge clk);

      // ---- observe delivered output (pre-edge = cycle just completed)
      if (out_valid === 1'b1 && out_ready === 1'b1) begin
        delivered = delivered + 1;
        checks    = checks + 1;
        got       = out_code;
        if (exp_q.size() == 0) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got, 8'hxx);
        end else begin
          expct  = exp_q[0];
          stim_v = stim_q[0];
          exp_q.pop_front();
          stim_q.pop_front();
          if (got !== expct) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", stim_v, got, expct);
          end
        end
      end

      // ---- REQ-IF-05: held stable while out_valid && !out_ready
      if (prev_ov === 1'b1 && prev_or === 1'b0) begin
        checks = checks + 1;
        if (out_valid !== 1'b1 || out_code !== prev_code) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", prev_stim, out_code, prev_code);
        end
      end

      // ---- acceptance (in_valid && in_ready sampled at this edge)
      if (in_valid === 1'b1 && in_ready === 1'b1) begin
        accepted = accepted + 1;
        stim_q.push_back(in_pcm);
        exp_q.push_back(ref_enc(in_pcm));
        offer = 1'b0;
        gap   = $urandom_range(0, 2);
      end

      // ---- drive next stimulus, with pseudo-random in_valid gaps
      if (offer === 1'b0) begin
        if (gap > 0) begin
          gap      = gap - 1;
          in_valid <= 1'b0;
        end else if (i < N_STIM) begin
          in_pcm   <= stim_at(i);
          in_valid <= 1'b1;
          offer    = 1'b1;
          i        = i + 1;
        end else begin
          in_valid <= 1'b0;
        end
      end else begin
        in_valid <= 1'b1;
      end

      // ---- pseudo-random out_ready stalls
      if (stall > 0) begin
        stall    = stall - 1;
        out_ready <= 1'b0;
      end else if ($urandom_range(0, 3) == 0) begin
        stall    = $urandom_range(1, 3);
        out_ready <= 1'b0;
      end else begin
        out_ready <= 1'b1;
      end

      prev_ov   = out_valid;
      prev_or   = out_ready;
      prev_code = out_code;
      prev_stim = (stim_q.size() > 0) ? stim_q[0] : 16'h0000;

      if (i >= N_STIM && offer === 1'b0 && exp_q.size() == 0 && out_valid === 1'b0)
        idle_cnt = idle_cnt + 1;
      else
        idle_cnt = 0;

      if (idle_cnt >= 16) break;
    end

    // ---- no loss, no duplication, no invention
    got_cnt = delivered;
    exp_cnt = accepted;
    checks  = checks + 1;
    if (accepted !== N_STIM || delivered !== accepted) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got_cnt, exp_cnt);
    end

    if (errors === 0)
      $display("RESULT PASS checks=%0d", checks);
    else
      $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
