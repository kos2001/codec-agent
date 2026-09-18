module tb_g711_alaw_enc;

  logic               clk;
  logic               rst_n;
  logic               in_valid;
  logic               in_ready;
  logic signed [15:0] in_pcm;
  logic               out_valid;
  logic               out_ready;
  logic [7:0]         out_code;

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
  always #5 clk = ~clk;          // posedge at 5,15,25,..  negedge at 10,20,..

  localparam integer CYC_LIMIT = 5000000;
  localparam integer ERR_PRINT = 100;   // cap log flood from a broken DUT

  integer seed, errors, checks, cyc;
  integer accepted, delivered, intended, send_idx;
  integer gap_cnt, stall_cnt, nstim, k, m, p;

  logic [7:0]  exp_q [$];
  logic [15:0] stim_q [$];

  logic        iv_d, or_d, ov_s, prev_held, done;
  logic [7:0]  oc_s, prev_code, expc, ref_c;
  logic [15:0] stm;
  logic signed [15:0] V;

  logic signed [15:0] stims [0:70000];

  // ---------------------------------------------------------------- report
  task automatic report (input logic [15:0] s, input logic [7:0] g, input logic [7:0] e);
    begin
      errors = errors + 1;
      if (errors <= ERR_PRINT)
        $display("ERROR stim=%h got=%h exp=%h", s, g, e);
    end
  endtask

  // ------------------------------------------------- reference encoder model
  function automatic logic [7:0] ref_enc (input logic signed [15:0] pcm);
    logic [12:0] x13;
    logic [12:0] mag;
    logic        sgn;
    logic [2:0]  seg;
    logic [3:0]  stp;
    begin
      x13 = pcm[15:3];                       // == ($signed(pcm) >>> 3)[12:0]
      if (pcm[15]) begin
        sgn = 1'b0;
        mag = ~x13;                          // -x13 - 1, in 13 bits
      end else begin
        sgn = 1'b1;
        mag = x13;
      end
      // mag is always in 0..4095 (REQ-A-03 holds inherently for 16-bit input)
      if (mag < 13'd32) begin
        seg = 3'd0;  stp = mag[4:1];
      end else if (mag < 13'd64) begin
        seg = 3'd1;  stp = (mag >> 1) - 5'd16;
      end else if (mag < 13'd128) begin
        seg = 3'd2;  stp = (mag >> 2) - 5'd16;
      end else if (mag < 13'd256) begin
        seg = 3'd3;  stp = (mag >> 3) - 5'd16;
      end else if (mag < 13'd512) begin
        seg = 3'd4;  stp = (mag >> 4) - 5'd16;
      end else if (mag < 13'd1024) begin
        seg = 3'd5;  stp = (mag >> 5) - 5'd16;
      end else if (mag < 13'd2048) begin
        seg = 3'd6;  stp = (mag >> 6) - 5'd16;
      end else begin
        seg = 3'd7;  stp = (mag >> 7) - 5'd16;
      end
      ref_enc = {sgn, seg, stp} ^ 8'h55;
    end
  endfunction

  // ------------------------------------------------------------- watchdog
  initial begin : tb_watchdog
    #200_000_000;
    $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

  // ------------------------------------------------------------------ main
  initial begin : tb_main
    seed      = 32'h0BADF00D;
    errors    = 0;
    checks    = 0;
    cyc       = 0;
    accepted  = 0;
    delivered = 0;
    prev_held = 1'b0;
    prev_code = 8'h00;
    gap_cnt   = 0;
    stall_cnt = 0;
    done      = 1'b0;
    k         = $urandom(seed);            // fixed seed, keeps the return quiet

    // ---- stimulus list: exhaustive 16-bit sweep + boundaries -------------
    m = 0;
    for (k = 0; k < 65536; k = k + 1) begin
      stims[m] = k - 32768;
      m = m + 1;
    end
    for (k = 0; k < 7; k = k + 1) begin         // 13-bit segment transitions
      p = 256 << k;                             // 256,512,1024,..,16384
      stims[m] = p - 8;      m = m + 1;
      stims[m] = p - 1;      m = m + 1;
      stims[m] = p;          m = m + 1;
      stims[m] = p + 7;      m = m + 1;
      stims[m] = -(p - 8);   m = m + 1;
      stims[m] = -p;         m = m + 1;
      stims[m] = -(p + 7);   m = m + 1;
    end
    stims[m] =  16'sd0;      m = m + 1;
    stims[m] =  16'sd1;      m = m + 1;
    stims[m] = -16'sd1;      m = m + 1;
    stims[m] =  16'sd7;      m = m + 1;
    stims[m] =  16'sd8;      m = m + 1;
    stims[m] = -16'sd8;      m = m + 1;
    stims[m] = -16'sd9;      m = m + 1;
    stims[m] =  16'sd15;     m = m + 1;
    stims[m] =  16'sd16;     m = m + 1;
    stims[m] = -16'sd16;     m = m + 1;
    stims[m] = -16'sd17;     m = m + 1;
    stims[m] =  16'sd4095;   m = m + 1;
    stims[m] =  16'sd4096;   m = m + 1;
    stims[m] = -16'sd4096;   m = m + 1;
    stims[m] = -16'sd4097;   m = m + 1;
    stims[m] =  16'sd32767;  m = m + 1;
    stims[m] =  16'sd32766;  m = m + 1;
    stims[m] = -16'sd32767;  m = m + 1;
    stims[m] =  16'h8000;    m = m + 1;   // -32768, saturation / min
    nstim    = m;
    intended = m;

    // ---- reset: hostile environment (in_valid=1, out_ready=0) ------------
    rst_n     = 1'b0;
    in_valid  = 1'b1;
    out_ready = 1'b0;
    in_pcm    = 16'sd32767;
    for (k = 0; k < 4; k = k + 1) begin
      @(negedge clk);
      checks = checks + 1;
      if (out_valid !== 1'b0)
        report(in_pcm, {7'b0, out_valid}, 8'h00);
    end
    @(negedge clk);
    rst_n     = 1'b1;                  // synchronous release
    in_valid  = 1'b0;
    out_ready = 1'b0;
    checks = checks + 1;
    if (out_valid !== 1'b0)            // REQ-IF-04: cleared after reset
      report(16'h0000, {7'b0, out_valid}, 8'h00);

    // ---- streaming sweep with random gaps and random backpressure -------
    send_idx = 0;
    while (!done) begin

      // observe outputs registered by the posedge that just came
      ov_s = out_valid;
      oc_s = out_code;

      // REQ-IF-05: out_valid && !out_ready must hold data stable
      if (prev_held) begin
        checks = checks + 1;
        if (ov_s !== 1'b1)
          report(16'hxxxx, {7'b0, ov_s}, prev_code);
        else if (oc_s !== prev_code)
          report(16'hxxxx, oc_s, prev_code);
      end

      // drive the next cycle
      iv_d = 1'b0;
      or_d = 1'b0;
      if (send_idx < nstim) begin
        in_pcm = stims[send_idx];
        if (gap_cnt > 0) begin
          iv_d    = 1'b0;
          gap_cnt = gap_cnt - 1;
        end else begin
          iv_d = 1'b1;
          if ($urandom_range(0, 6) == 0) gap_cnt = $urandom_range(1, 3);
        end
      end
      if (stall_cnt > 0) begin
        or_d      = 1'b0;
        stall_cnt = stall_cnt - 1;
      end else begin
        or_d = 1'b1;
        if ($urandom_range(0, 3) == 0) stall_cnt = $urandom_range(1, 4);
      end
      in_valid  = iv_d;
      out_ready = or_d;
      #1;                                     // let in_ready settle (comb)

      // contract handshake equation
      checks = checks + 1;
      if (in_ready !== ((~ov_s) | or_d))
        report(in_pcm, {7'b0, in_ready}, {7'b0, ((~ov_s) | or_d)});

      // acceptance at the upcoming posedge
      if ((iv_d === 1'b1) && ((ov_s === 1'b0) || (or_d === 1'b1))) begin
        ref_c = ref_enc(in_pcm);
        exp_q.push_back(ref_c);
        stim_q.push_back(in_pcm);
        accepted = accepted + 1;
        send_idx = send_idx + 1;
      end

      // delivery at the upcoming posedge
      if ((ov_s === 1'b1) && (or_d === 1'b1)) begin
        delivered = delivered + 1;
        checks    = checks + 1;
        if (exp_q.size() == 0) begin
          report(16'hxxxx, oc_s, 8'h00);
        end else begin
          expc = exp_q[0];
          exp_q.pop_front();
          stm  = stim_q[0];
          stim_q.pop_front();
          if (oc_s !== expc)
            report(stm, oc_s, expc);
        end
      end

      prev_held = (ov_s === 1'b1) && (or_d === 1'b0);
      prev_code = oc_s;

      if ((send_idx >= nstim) && (exp_q.size() == 0) && (ov_s === 1'b0)) begin
        done = 1'b1;
      end else begin
        cyc = cyc + 1;
        if (cyc > CYC_LIMIT) begin
          $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
          $finish;
        end
        @(negedge clk);
      end
    end

    // ---- synchronous reset semantics with a held output (REQ-IF-04) -----
    in_valid  = 1'b0;
    out_ready = 1'b0;
    in_pcm    = 16'sd0;
    repeat (2) @(negedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0)
      report(16'h0000, {7'b0, out_valid}, 8'h00);

    V         = 16'sd1000;
    in_pcm    = V;
    in_valid  = 1'b1;
    out_ready = 1'b0;                 // hold the output
    @(negedge clk);                   // one latency cycle later
    ref_c = ref_enc(V);
    checks = checks + 1;
    if ((out_valid !== 1'b1) || (out_code !== ref_c))
      report(V, out_code, ref_c);

    in_valid = 1'b0;
    rst_n    = 1'b0;                  // drop reset between two clock edges
    #2;                               // still before the next posedge
    checks = checks + 1;
    if (out_valid !== 1'b1)           // async reset would fail here
      report(V, {7'b0, out_valid}, 8'h01);
    @(posedge clk);
    #1;
    checks = checks + 1;
    if (out_valid !== 1'b0)           // cleared by the synchronous reset edge
      report(V, {7'b0, out_valid}, 8'h00);
    @(negedge clk);
    rst_n = 1'b1;

    // ---- flow accounting -------------------------------------------------
    checks = checks + 1;
    if ((accepted !== intended) || (delivered !== intended)) begin
      errors = errors + 1;
      if (errors <= ERR_PRINT)
        $display("MISMATCH count accepted=%0d delivered=%0d intended=%0d",
                 accepted, delivered, intended);
    end

    if (errors == 0)
      $display("RESULT PASS checks=%0d", checks);
    else
      $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
