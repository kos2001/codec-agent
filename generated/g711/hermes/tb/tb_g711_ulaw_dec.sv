module tb_g711_ulaw_dec;

  // ---------------- DUT connections ----------------
  logic        clk;
  logic        rst_n;
  logic        in_valid;
  logic        in_ready;
  logic [7:0]  in_code;
  logic        out_valid;
  logic        out_ready;
  logic [15:0] out_pcm;

  // ---------------- TB state (all module level) ----------------
  logic [7:0]  stim_q [$];
  logic [15:0] exp_q [$];
  logic [7:0]  exp_src_q [$];
  logic [15:0] hold_pcm;
  logic [15:0] pcm_pre;
  logic [15:0] exp_v;
  logic [7:0]  src_v;
  logic        ov_pre;      // out_valid during the previous cycle (pre-posedge)
  logic        or_pre;      // out_ready during the previous cycle (pre-posedge)
  logic        hold_valid;
  integer      checks;
  integer      errors;
  integer      accepted;
  integer      delivered;
  integer      total_stim;
  integer      gap;
  integer      wcnt;
  integer      seed;
  integer      i;
  integer      j;

  localparam integer NSTIM    = 264;     // 256 codes + 8 boundary/sign extras
  localparam integer CLK_HALF = 5;
  localparam integer MAXCYC   = 100000;  // bounded so a broken DUT cannot hang

  // ---------------- DUT ----------------
  g711_ulaw_dec dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (in_valid),
    .in_ready  (in_ready),
    .in_code   (in_code),
    .out_valid (out_valid),
    .out_ready (out_ready),
    .out_pcm   (out_pcm)
  );

  // ---------------- reference model ----------------
  // REQ-U-05: transmitted bit7 = sign, 1 = positive (not inverted).
  // REQ-U-06: bits 6..0 are the inverted magnitude {segment, step}.
  // REQ-U-07: reconstruction = ((2*(16+step)+1) << segment) - 33 on the 14-bit scale.
  // REQ-U-08: out_pcm = reconstruction << 2, negated when the sign bit is 0.
  function automatic logic signed [15:0] ulaw_decode(input logic [7:0] code);
    logic [6:0]  magv;
    logic [2:0]  seg;
    logic [3:0]  step;
    logic [14:0] recon;
    logic [15:0] lin;
    begin
      magv  = ~code[6:0];
      seg   = magv[6:4];
      step  = magv[3:0];
      recon = ((2 * (16 + step) + 1) << seg) - 33;
      lin   = recon << 2;                       // <= 32124, fits as positive 15-bit
      ulaw_decode = code[7] ? $signed({1'b0, lin[14:0]})
                            : -$signed({1'b0, lin[14:0]});
    end
  endfunction

  always #CLK_HALF clk = ~clk;

  // ---------------- watchdog ----------------
  initial begin
    #5000000;
    $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

  // ---------------- main ----------------
  initial begin
    clk        = 1'b0;
    rst_n      = 1'b0;
    in_valid   = 1'b0;
    out_ready  = 1'b0;
    in_code    = 8'h00;
    checks     = 0;
    errors     = 0;
    accepted   = 0;
    delivered  = 0;
    total_stim = 0;
    gap        = 0;
    wcnt       = 0;
    hold_valid = 1'b0;
    hold_pcm   = 16'h0000;
    pcm_pre    = 16'h0000;
    ov_pre     = 1'b0;
    or_pre     = 1'b0;
    exp_v      = 16'h0000;
    src_v      = 8'h00;
    seed       = 32'd20240918;
    i          = $urandom(seed);      // one seeding call, then $urandom with no arg
    j          = 0;

    // ---------- 1. reset, hostile environment (in_valid=1, out_ready=0) ----------
    rst_n = 1'b0; in_valid = 1'b1; out_ready = 1'b0; in_code = 8'hA5;   // REQ-IF-04
    repeat (4) @(negedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0) begin errors = errors + 1; $display("ERROR out_valid=%b after reset", out_valid); end
    checks = checks + 1;
    if (in_ready !== 1'b1) begin errors = errors + 1; $display("ERROR in_ready=%b during reset", in_ready); end
    repeat (2) @(negedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0) begin errors = errors + 1; $display("ERROR out_valid=%b during hostile reset", out_valid); end

    // ---------- 2. REQ-IF-04 is a SYNCHRONOUS reset ----------
    in_code = 8'h3C;
    rst_n = 1'b1; in_valid = 1'b1; out_ready = 1'b0;
    @(negedge clk);                  // sample accepted at the next posedge
    @(negedge clk);                  // output now held
    checks = checks + 1;
    if (out_valid !== 1'b1) begin errors = errors + 1; $display("ERROR out_valid=%b expected held output", out_valid); end
    checks = checks + 1;
    if (in_ready !== 1'b0) begin errors = errors + 1; $display("ERROR in_ready=%b with held output", in_ready); end
    rst_n = 1'b0;                    // dropped between two clock edges
    #1;                              // settle: no clock edge occurs
    checks = checks + 1;
    if (out_valid !== 1'b1) begin errors = errors + 1; $display("ERROR out_valid cleared before the clock edge (asynchronous reset)"); end
    @(posedge clk);
    @(negedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0) begin errors = errors + 1; $display("ERROR out_valid=%b not cleared at the clock edge", out_valid); end
    rst_n = 1'b1;

    // ---------- 3. stimuli ----------
    for (i = 0; i < 256; i = i + 1) stim_q.push_back(i[7:0]);   // every code, REQ-IF-02
    stim_q.push_back(8'h00);   // negative full scale (sign bit 0)
    stim_q.push_back(8'h7F);   // negative zero, REQ-U-02
    stim_q.push_back(8'hFF);   // positive zero, REQ-U-06
    stim_q.push_back(8'h80);   // positive full scale, REQ-U-08
    stim_q.push_back(8'h81);   // first negative segment code
    stim_q.push_back(8'hFE);   // smallest positive magnitude step
    stim_q.push_back(8'h10);   // segment/step boundary
    stim_q.push_back(8'h8F);   // segment/step boundary, negative side

    // clean state before the streaming phase
    in_valid = 1'b0; out_ready = 1'b0; in_code = 8'h00;
    rst_n = 1'b0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    accepted = 0; delivered = 0; total_stim = 0; gap = 0; wcnt = 0;
    hold_valid = 1'b0; ov_pre = 1'b0; or_pre = 1'b0; pcm_pre = 16'h0000;
    in_valid = 1'b0; out_ready = 1'b0; in_code = 8'h00;

    // ---------- 4. streaming handshake phase ----------
    while (((total_stim < NSTIM) || (accepted > delivered) || ov_pre || out_valid || in_valid)
           && (wcnt < MAXCYC)) begin
      @(negedge clk);
      wcnt = wcnt + 1;

      // handshake contract checked on the live combinational value
      checks = checks + 1;
      if (in_ready !== (~out_valid | out_ready)) begin
        errors = errors + 1;
        $display("ERROR in_ready=%b exp=%b", in_ready, (~out_valid | out_ready));
      end

      // ---- delivery that happened at the clock edge just passed ----
      if (ov_pre && or_pre) begin
        if (exp_q.size() == 0) begin
          checks = checks + 1; errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, pcm_pre, 16'h0000);
        end else begin
          exp_v = exp_q[0];     exp_q.pop_front();
          src_v = exp_src_q[0]; exp_src_q.pop_front();
          checks = checks + 1;
          if (pcm_pre !== exp_v) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", {8'h00, src_v}, pcm_pre, exp_v);
          end
          delivered = delivered + 1;
        end
        hold_valid = 1'b0;
      end else if (ov_pre) begin
        // ---- REQ-IF-05: data stable while out_valid && !out_ready ----
        if (hold_valid) begin
          checks = checks + 1;
          if (pcm_pre !== hold_pcm) begin
            errors = errors + 1;
            $display("ERROR held data got=%h exp=%h", pcm_pre, hold_pcm);
          end
        end else begin
          hold_valid = 1'b1;
          hold_pcm   = pcm_pre;
        end
      end else begin
        hold_valid = 1'b0;
      end

      // ---- acceptance that happened at the clock edge just passed ----
      if (in_valid && (~ov_pre | or_pre)) begin
        exp_q.push_back(ulaw_decode(in_code));   // REQ-IF-01 / REQ-IF-02
        exp_src_q.push_back(in_code);
        accepted = accepted + 1;
        in_valid = 1'b0;
      end

      // ---- drive for the next clock edge ----
      out_ready = (($urandom % 3) != 0);         // pseudo-random stalls
      if (gap > 0) gap = gap - 1;
      if (!in_valid && (total_stim < NSTIM) && (gap == 0) && (stim_q.size() > 0)) begin
        in_code = stim_q[0];                     // REQ-IF-02
        stim_q.pop_front();
        in_valid = 1'b1;
        total_stim = total_stim + 1;
        if (($urandom % 3) == 0) gap = 1 + ($urandom % 3);   // pseudo-random valid gaps
      end

      // capture pre-edge values for the next iteration
      ov_pre  = out_valid;
      or_pre  = out_ready;
      pcm_pre = out_pcm;
    end

    // ---------- 5. accounting ----------
    checks = checks + 1;
    if (accepted !== NSTIM) begin errors = errors + 1; $display("ERROR accepted=%0d exp=%0d", accepted, NSTIM); end
    checks = checks + 1;
    if (delivered !== accepted) begin errors = errors + 1; $display("ERROR delivered=%0d accepted=%0d", delivered, accepted); end
    checks = checks + 1;
    if (total_stim !== NSTIM) begin errors = errors + 1; $display("ERROR stimuli issued=%0d exp=%0d", total_stim, NSTIM); end
    checks = checks + 1;
    if (exp_q.size() != 0) begin errors = errors + 1; $display("ERROR %0d expected samples undelivered", exp_q.size()); end
    checks = checks + 1;
    if (out_valid !== 1'b0) begin errors = errors + 1; $display("ERROR out_valid=%b stuck at end", out_valid); end

    if (errors == 0) $display("RESULT PASS checks=%0d", checks);
    else             $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
