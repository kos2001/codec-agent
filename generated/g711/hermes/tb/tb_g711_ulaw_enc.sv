module tb_g711_ulaw_enc;

  localparam integer MAX_CYCLES = 500000;   // per-phase cycle cap
  localparam integer WD_LIMIT   = 2000000;  // backstop watchdog cycles

  // ---------------- DUT interface ----------------
  logic               clk;
  logic               rst_n;
  logic               in_valid;
  logic               in_ready;
  logic signed [15:0] in_pcm;
  logic               out_valid;
  logic               out_ready;
  logic [7:0]         out_code;

  g711_ulaw_enc dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (in_valid),
    .in_ready  (in_ready),
    .in_pcm    (in_pcm),
    .out_valid (out_valid),
    .out_ready (out_ready),
    .out_code  (out_code)
  );

  // ---------------- reference model ----------------
  function automatic logic [7:0] ulaw_enc_ref (input logic signed [15:0] x16);
    logic signed [15:0] x14;
    logic [15:0] mag;
    int   v, s, step;
    logic sgn;
    begin
      x14  = x16 >>> 2;                       // REQ-U-01 : 14-bit scale
      sgn  = ~x14[15];                        // REQ-U-05 : 1 = positive
      mag  = x14[15] ? (~x14) : x14;          // REQ-U-02 : -x14-1 for negatives
      v    = mag + 33;                        // REQ-U-03
      if (v > 8191) v = 8191;                 // saturation
      s = 0;
      while ((s < 7) && (v > ((64 << s) - 1))) s = s + 1;   // REQ-U-04
      step = (v - (32 << s)) >> (s + 1);
      ulaw_enc_ref = {sgn, ~{s[2:0], step[3:0]}};           // REQ-U-06
    end
  endfunction

  // ---------------- module-level state (no decls in initial/always) ----------------
  integer      checks, errors;
  integer      acc_count, del_count, total_intended;
  integer      i, k, b, cyc, dcyc, wd_count;
  integer      rnd1, rnd2;
  integer      rng_seed, rng_dummy;
  logic [31:0] bt;
  logic [15:0] stim_q [$];
  logic [7:0]  exp_q [$];
  logic [15:0] exp_stim_q [$];
  logic [15:0] cur_stim, stim_ref;
  integer      stim_idx;
  logic        pending, holding, abort_flag, finished, synst;
  logic [7:0]  obs_code, hold_code, exp_code;
  logic        obs_valid, obs_in_ready;

  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  // watchdog (also catches a DUT that never accepts anything)
  always @(posedge clk) begin
    if (!rst_n) begin
      wd_count <= 0;
    end else begin
      wd_count <= wd_count + 1;
      if ((wd_count > WD_LIMIT) && !finished) begin
        $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
        $finish;
      end
    end
  end

  initial begin
    checks = 0; errors = 0; acc_count = 0; del_count = 0;
    stim_idx = 0; pending = 1'b0; holding = 1'b0;
    abort_flag = 1'b0; finished = 1'b0;
    rng_seed = 32'h00C0FFEE;
    rng_dummy = $urandom(rng_seed);           // single seeding call

    // ---- Phase A: hostile reset environment (REQ-IF-04) ----
    rst_n    = 1'b0;
    in_valid = 1'b1;   // hostile: traffic asserted during reset
    out_ready = 1'b0;
    in_pcm   = 16'h1234;
    for (k = 0; k < 4; k = k + 1) begin
      @(negedge clk);
      #1;
      checks = checks + 1;
      if (out_valid !== 1'b0) begin
        errors = errors + 1;
        $display("ERROR reset: out_valid=%b expected 0", out_valid);
      end
    end

    // ---- Phase B: synchronous reset must not clear between edges ----
    rst_n    = 1'b1;
    in_valid = 1'b1;
    out_ready = 1'b0;
    in_pcm   = 16'h00A5;
    synst = 1'b0;
    for (k = 0; k < 20; k = k + 1) begin
      @(negedge clk);
      #1;
      if ((out_valid === 1'b1) && !synst) begin
        synst = 1'b1;
        k = 20;
      end
    end
    if (!synst) begin
      errors = errors + 1;
      $display("ERROR sync-reset: out_valid never asserted while waiting");
    end else begin
      hold_code = out_code;
      rst_n = 1'b0;                 // drop strictly between two clock edges
      #1;
      checks = checks + 1;
      if (out_valid !== 1'b1) begin
        errors = errors + 1;
        $display("ERROR sync-reset: out_valid cleared before a clock edge (async reset?)");
      end
      @(posedge clk);
      #1;
      checks = checks + 1;
      if (out_valid !== 1'b0) begin
        errors = errors + 1;
        $display("ERROR sync-reset: out_valid not cleared at the clock edge");
      end
      @(negedge clk);
      rst_n = 1'b1;
    end

    // ---- Phase C: stimulus list (exhaustive + boundaries) ----
    for (i = -32768; i <= 32767; i = i + 1) stim_q.push_back(i[15:0]);

    stim_q.push_back(16'h0000); stim_q.push_back(16'h0001);
    stim_q.push_back(16'h0002); stim_q.push_back(16'h0003);
    stim_q.push_back(16'h0004); stim_q.push_back(16'hFFFF);
    stim_q.push_back(16'hFFFE); stim_q.push_back(16'hFFFC);
    stim_q.push_back(16'h7FFF); stim_q.push_back(16'h8000);

    for (b = 0; b < 8; b = b + 1) begin
      bt = 4*(32<<b);            stim_q.push_back(bt[15:0]);  // segment low edge
      bt = 4*(32<<b) - 4;        stim_q.push_back(bt[15:0]);
      bt = 4*(32<<b) - 5;        stim_q.push_back(bt[15:0]);
      bt = 4*((64<<b) - 1);      stim_q.push_back(bt[15:0]);  // segment top
      bt = 4*((64<<b) - 1) - 4;  stim_q.push_back(bt[15:0]);
      bt = 4*(64<<b);            stim_q.push_back(bt[15:0]);  // next segment low
      bt = -4*(32<<b);           stim_q.push_back(bt[15:0]);
      bt = -4*((64<<b) - 1);     stim_q.push_back(bt[15:0]);
    end

    total_intended = stim_q.size();

    // ---- Phase C: streaming sweep with random gaps and stalls ----
    in_valid  = 1'b0;
    out_ready = 1'b0;
    in_pcm    = 16'h0000;
    stim_idx  = 0;
    pending   = 1'b0;
    holding   = 1'b0;
    cyc       = 0;

    while ((stim_idx < total_intended) && !abort_flag) begin
      @(negedge clk);
      if (!pending) cur_stim = stim_q[stim_idx];
      in_pcm = cur_stim;

      rnd1 = $urandom();
      if (rnd1 < 0) rnd1 = -rnd1;
      rnd2 = $urandom();
      if (rnd2 < 0) rnd2 = -rnd2;
      in_valid  = ((rnd1 % 100) < 75);
      out_ready = ((rnd2 % 100) < 75);

      #1;
      obs_valid    = out_valid;
      obs_code     = out_code;
      obs_in_ready = in_ready;

      // held-data stability (REQ-IF-05), checked on the cycle after the stall
      if (holding) begin
        checks = checks + 1;
        if (!obs_valid || (obs_code !== hold_code)) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h",
                   (exp_stim_q.size() > 0) ? exp_stim_q[0] : cur_stim,
                   obs_code, hold_code);
        end
        holding = 1'b0;
      end

      // in_ready contract
      checks = checks + 1;
      if (obs_in_ready !== (~obs_valid | out_ready)) begin
        errors = errors + 1;
        $display("ERROR in_ready=%b expected=%b (out_valid=%b out_ready=%b)",
                 obs_in_ready, (~obs_valid | out_ready), obs_valid, out_ready);
      end

      // acceptance
      if (in_valid && obs_in_ready) begin
        exp_q.push_back(ulaw_enc_ref(cur_stim));
        exp_stim_q.push_back(cur_stim);
        acc_count = acc_count + 1;
        stim_idx  = stim_idx + 1;
        pending   = 1'b0;
      end else begin
        pending = 1'b1;
      end

      // delivery
      if (obs_valid && out_ready) begin
        del_count = del_count + 1;
        checks = checks + 1;
        if (exp_q.size() == 0) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", cur_stim, obs_code, 8'hxx);
        end else begin
          exp_code = exp_q[0];       exp_q.pop_front();
          stim_ref = exp_stim_q[0];  exp_stim_q.pop_front();
          if (obs_code !== exp_code) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", stim_ref, obs_code, exp_code);
          end
        end
      end else if (obs_valid && !out_ready) begin
        hold_code = obs_code;
        holding   = 1'b1;
      end

      cyc = cyc + 1;
      if (cyc > MAX_CYCLES) abort_flag = 1'b1;
    end

    // ---- drain remaining expected outputs ----
    // NOTE: in_valid/out_ready are driven only immediately after a negedge.
    // Changing out_ready between negedge and posedge would let the DUT consume a
    // held sample at the very next posedge without this bench observing it.
    dcyc = 0;
    while ((exp_q.size() > 0) && (dcyc < 100000) && !abort_flag) begin
      @(negedge clk);
      in_valid  = 1'b0;
      out_ready = 1'b1;
      #1;
      obs_valid = out_valid;
      obs_code  = out_code;

      if (holding) begin
        checks = checks + 1;
        if (!obs_valid || (obs_code !== hold_code)) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h",
                   (exp_stim_q.size() > 0) ? exp_stim_q[0] : 16'h0000,
                   obs_code, hold_code);
        end
        holding = 1'b0;
      end

      if (obs_valid && out_ready) begin
        del_count = del_count + 1;
        checks = checks + 1;
        if (exp_q.size() == 0) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, obs_code, 8'hxx);
        end else begin
          exp_code = exp_q[0];       exp_q.pop_front();
          stim_ref = exp_stim_q[0];  exp_stim_q.pop_front();
          if (obs_code !== exp_code) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", stim_ref, obs_code, exp_code);
          end
        end
      end
      dcyc = dcyc + 1;
    end

    // ---- accounting ----
    checks = checks + 1;
    if (acc_count !== total_intended) begin
      errors = errors + 1;
      $display("ERROR accepted=%0d intended=%0d", acc_count, total_intended);
    end
    checks = checks + 1;
    if (del_count !== acc_count) begin
      errors = errors + 1;
      $display("ERROR delivered=%0d accepted=%0d", del_count, acc_count);
    end
    checks = checks + 1;
    if (del_count !== total_intended) begin
      errors = errors + 1;
      $display("ERROR delivered=%0d intended=%0d", del_count, total_intended);
    end

    if ((errors == 0) && (acc_count === total_intended) &&
        (del_count === total_intended) && !abort_flag)
      $display("RESULT PASS checks=%0d", checks);
    else
      $display("RESULT FAIL checks=%0d errors=%0d", errors == 0 ? 0 : errors);

    finished = 1'b1;
    $finish;
  end

endmodule
