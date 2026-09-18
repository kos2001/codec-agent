module tb_g711_alaw_enc;

  logic               clk;
  logic               rst_n;
  logic               in_valid;
  logic               in_ready;
  logic signed [15:0] in_pcm;
  logic               out_valid;
  logic               out_ready;
  logic [7:0]         out_code;

  integer seed;
  integer idx;
  integer accepted;
  integer delivered;
  integer checks;
  integer errors;

  logic [15:0] stim_q[$];
  logic [7:0]  exp_q[$];

  logic [15:0] stim_pop;
  logic [7:0]  exp_pop;
  logic [15:0] held_stim;
  logic [7:0]  held_code;
  logic        held_valid;
  logic        acc_last;
  logic        acc;
  logic        del;

  localparam integer TOTAL = 65536 + 40;

  // ------------------------------------------------------------------
  // Reference model: A-law encode, REQ-A-01 .. REQ-A-06
  // ------------------------------------------------------------------
  function automatic logic [7:0] alaw_enc_ref(input logic signed [15:0] pcm);
    integer xi;      // 13-bit scale value
    integer m;       // magnitude
    integer segi;    // 0..7
    integer sti;     // 0..15
    integer k;
    logic        sign_bit;
    logic [2:0]  segb;
    logic [3:0]  stb;
    begin
      xi = pcm;
      xi = xi >>> 3;                              // REQ-A-01
      if (xi >= 0) begin                          // REQ-A-05 sign bit
        sign_bit = 1'b1;
        m        = xi;
      end else begin
        sign_bit = 1'b0;
        m        = -xi - 1;                       // REQ-A-02
      end
      if (m > 4095) m = 4095;                     // REQ-A-03
      if (m < 32) begin                           // REQ-A-04 segment 0
        segi = 0;
        sti  = m >> 1;
      end else begin
        segi = 1;
        for (k = 1; k <= 7; k = k + 1)
          if (m >= (1 << (k + 4))) segi = k;
        sti = (m - (1 << (segi + 4))) >> segi;
      end
      segb = segi;
      stb  = sti;
      alaw_enc_ref = {sign_bit, segb, stb} ^ 8'h55;  // REQ-A-06
    end
  endfunction

  // ------------------------------------------------------------------
  // Stimulus sequence: exhaustive sweep then directed boundary values
  // ------------------------------------------------------------------
  function automatic logic [15:0] get_stim(input integer n);
    integer d;
    begin
      if (n < 65536) begin
        get_stim = n;
      end else begin
        d = n - 65536;
        case (d)
          0:  get_stim = 16'h0000;   // zero
          1:  get_stim = 16'h0001;
          2:  get_stim = 16'h0007;
          3:  get_stim = 16'h0008;
          4:  get_stim = 16'h000f;
          5:  get_stim = 16'h0010;
          6:  get_stim = 16'h00ff;
          7:  get_stim = 16'h0100;   // x13 = 32  seg0/seg1 edge
          8:  get_stim = 16'h011f;
          9:  get_stim = 16'h0120;
          10: get_stim = 16'h017f;
          11: get_stim = 16'h0180;
          12: get_stim = 16'h01ff;   // top of segment 1
          13: get_stim = 16'h0200;   // segment 2
          14: get_stim = 16'h03ff;
          15: get_stim = 16'h0400;
          16: get_stim = 16'h07ff;
          17: get_stim = 16'h0800;
          18: get_stim = 16'h0fff;
          19: get_stim = 16'h1000;
          20: get_stim = 16'h1fff;
          21: get_stim = 16'h2000;   // segment 6
          22: get_stim = 16'h3fff;   // top of segment 6
          23: get_stim = 16'h4000;   // segment 7
          24: get_stim = 16'h7ff8;   // x13 = 4095
          25: get_stim = 16'h7fff;   // saturation positive
          26: get_stim = 16'h8000;   // saturation negative
          27: get_stim = 16'h8001;
          28: get_stim = 16'h8008;
          29: get_stim = 16'h8007;
          30: get_stim = 16'hfff8;   // x13 = -1
          31: get_stim = 16'hffff;   // -1
          32: get_stim = 16'hfff0;
          33: get_stim = 16'hffe0;
          34: get_stim = 16'hff00;
          35: get_stim = 16'hfe00;
          36: get_stim = 16'hf000;
          37: get_stim = 16'hc000;
          38: get_stim = 16'h8080;
          39: get_stim = 16'h7c00;
          default: get_stim = 16'h0000;
        endcase
      end
    end
  endfunction

  // ------------------------------------------------------------------
  // DUT
  // ------------------------------------------------------------------
  g711_alaw_enc dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (in_valid),
    .in_ready  (in_ready),
    .in_pcm    (in_pcm),
    .out_valid (out_valid),
    .out_ready (out_ready),
    .out_code  (out_code)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  // ------------------------------------------------------------------
  // Watchdog
  // ------------------------------------------------------------------
  initial begin
    #10000000;
    $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

  // ------------------------------------------------------------------
  // Main stimulus + scoreboard (single process, pre-NBA sampling at posedge)
  // ------------------------------------------------------------------
  initial begin
    rst_n     = 1'b0;
    in_valid  = 1'b0;
    in_pcm    = 16'sd0;
    out_ready = 1'b0;
    seed      = 32'h0BAD_F00D;
    idx       = 0;
    accepted  = 0;
    delivered = 0;
    checks    = 0;
    errors    = 0;
    acc_last  = 1'b0;
    held_valid = 1'b0;
    held_code  = 8'h00;
    held_stim  = 16'h0000;
    acc = 1'b0;
    del = 1'b0;

    // reset for several cycles (REQ-IF-04)
    repeat (4) @(negedge clk);
    @(posedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {8'h00, out_code}, 8'h00);
    end
    @(negedge clk);
    rst_n = 1'b1;

    while (!(idx >= TOTAL && exp_q.size() == 0)) begin
      // ---- drive inputs for the upcoming posedge ----
      @(negedge clk);
      if (in_valid === 1'b0 || acc_last === 1'b1) begin
        if (idx >= TOTAL) in_valid = 1'b0;
        else              in_valid = (($urandom(seed) % 4) != 0);  // random gaps
      end
      in_pcm    = $signed(get_stim(idx));
      out_ready = (($urandom(seed) & 1) != 0);                     // random stalls

      // ---- sample at posedge using pre-NBA (just-ended cycle) values ----
      @(posedge clk);

      acc = (in_valid === 1'b1) && (in_ready === 1'b1);
      del = (out_valid === 1'b1) && (out_ready === 1'b1);

      if (acc === 1'b1) begin                       // push before pop
        stim_q.push_back(in_pcm);
        exp_q.push_back(alaw_enc_ref(in_pcm));
        idx      = idx + 1;
        accepted = accepted + 1;
      end

      if (del === 1'b1) begin
        checks = checks + 1;
        if (exp_q.size() == 0) begin                // invented output
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {8'h00, out_code}, 8'h00);
        end else begin
          stim_pop = stim_q[0];
          exp_pop  = exp_q[0];
          stim_q.pop_front();
          exp_q.pop_front();
          delivered = delivered + 1;
          if (out_code !== exp_pop) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", stim_pop, {8'h00, out_code}, {8'h00, exp_pop});
          end
        end
      end

      // ---- hold-stability while out_valid && !out_ready (REQ-IF-05) ----
      if (out_valid === 1'b1 && out_ready === 1'b0) begin
        checks = checks + 1;
        if (held_valid === 1'b1 && out_code !== held_code) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", held_stim, {8'h00, out_code}, {8'h00, held_code});
        end
        held_code  = out_code;
        held_stim  = (stim_q.size() > 0) ? stim_q[0] : 16'h0000;
        held_valid = 1'b1;
      end else begin
        held_valid = 1'b0;
        held_code  = 8'h00;
        held_stim  = 16'h0000;
      end

      acc_last = acc;
    end

    // ---- conservation: no loss, no duplication (REQ-IF-05) ----
    checks = checks + 1;
    if (accepted != delivered) errors = errors + 1;

    if (errors == 0) $display("RESULT PASS checks=%0d", checks);
    else             $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
