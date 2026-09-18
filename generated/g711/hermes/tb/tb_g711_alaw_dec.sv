module tb_g711_alaw_dec;

  localparam integer NB    = 24;
  localparam integer TOTAL = 256 + NB + 64;

  logic               clk;
  logic               rst_n;
  logic               in_valid;
  logic [7:0]         in_code;
  logic               out_ready;
  logic               out_valid;
  logic               in_ready;
  logic signed [15:0] out_pcm;

  g711_alaw_dec dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (in_valid),
    .in_ready  (in_ready),
    .in_code   (in_code),
    .out_valid (out_valid),
    .out_ready (out_ready),
    .out_pcm   (out_pcm)
  );

  // ---------- module-level state (Icarus: no decls inside initial/always) ----------
  logic [31:0] checks, errors, accepted, delivered, offered, st_idx, gap_left;
  logic [31:0] wd, tries;
  integer      seed;
  logic [31:0] rnd;
  logic [7:0]  bnd [0:NB-1];

  logic signed [15:0] exp_q [$];
  logic [7:0]         stim_q [$];

  logic signed [15:0] op_s, e16, watch_pcm;
  logic        ov_s, dlv_now, acc_last, acc_pend, hold_fail;
  logic [7:0]  c8;
  logic [15:0] got16, exp16;

  // ---------- reference model: 8-bit A-law character -> 16-bit PCM ----------
  // The line character carries even bits inverted (REQ-A-06); undo that first,
  // then decode segment/step/sign from the un-inverted code.
  function automatic logic signed [15:0] alaw_dec_ref(input logic [7:0] code);
    logic [7:0]  c;
    logic [3:0]  seg;
    logic [3:0]  stp;
    logic [12:0] recon;
    logic [15:0] mag;
    begin
      c   = code ^ 8'h55;                                   // REQ-A-06 inverse (XOR 0x55)
      seg = c[6:4];
      stp = c[3:0];
      if (seg == 4'd0)
        recon = 13'd1 + ({9'd0, stp} << 1);                 // REQ-A-07: 2*step+1
      else
        recon = 13'd1 + ((13'd16 + {9'd0, stp}) << 1);      // REQ-A-07: 2*(16+step)+1
      if (seg != 4'd0)
        recon = recon << (seg - 4'd1);                      // REQ-A-07: << (s-1)
      mag = {3'b000, recon} << 3;                           // REQ-A-08: 13-bit -> 16-bit
      if (c[7]) alaw_dec_ref = $signed(mag);                // REQ-A-05: bit7 = 1 positive
      else      alaw_dec_ref = -$signed(mag);               // REQ-A-08: negated when sign=0
    end
  endfunction

  // ---------- clock ----------
  initial clk = 1'b0;
  always #5 clk = ~clk;

  // ---------- watchdog ----------
  always @(posedge clk) begin
    wd = wd + 1;
    if (wd > 200000) begin
      $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
      $finish;
    end
  end

  // ---------- main sequence ----------
  initial begin
    wd = 0; checks = 0; errors = 0;
    accepted = 0; delivered = 0; offered = 0; st_idx = 0; gap_left = 1;
    seed = 12345;
    rnd  = $urandom(seed);            // seed once; plain $urandom() afterwards
    in_valid  = 1'b0;
    in_code   = 8'h00;
    out_ready = 1'b1;
    rst_n     = 1'b0;
    ov_s = 1'b0; op_s = 16'sh0000; acc_last = 1'b0; hold_fail = 1'b0;

    // boundary / segment / saturation / sign emphasis codes
    bnd[0]=8'h00; bnd[1]=8'h01; bnd[2]=8'h0F; bnd[3]=8'h10;
    bnd[4]=8'h1F; bnd[5]=8'h20; bnd[6]=8'h3F; bnd[7]=8'h40;
    bnd[8]=8'h7F; bnd[9]=8'h80; bnd[10]=8'h81; bnd[11]=8'h8F;
    bnd[12]=8'h90; bnd[13]=8'h9F; bnd[14]=8'hA0; bnd[15]=8'hAA;
    bnd[16]=8'hBF; bnd[17]=8'hC0; bnd[18]=8'hD5; bnd[19]=8'hFE;
    bnd[20]=8'hFF; bnd[21]=8'h55; bnd[22]=8'h2A; bnd[23]=8'h15;

    // --- REQ-IF-04: reset dominates a hostile environment ---
    in_valid = 1'b1; in_code = 8'h5A; out_ready = 1'b0;
    repeat (4) begin
      @(posedge clk); #1;
      checks = checks + 1;
      if (out_valid !== 1'b0) begin
        errors = errors + 1;
        $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {15'b0, out_valid}, 16'h0000);
      end
    end

    // release reset, drain anything the hostile phase latched (not counted)
    @(negedge clk);
    rst_n = 1'b1; in_valid = 1'b0; out_ready = 1'b1; in_code = 8'h00;
    repeat (6) @(negedge clk);

    // --- main phase: every code + boundaries + random, random gaps/stalls ---
    out_ready = 1'b0;
    ov_s = out_valid; op_s = out_pcm;

    while (delivered < TOTAL) begin
      @(negedge clk);

      // delivery that occurred at the posedge just taken
      dlv_now = ov_s & out_ready;
      if (dlv_now) begin
        delivered = delivered + 1;
        checks    = checks + 1;
        if (exp_q.size() == 0) begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, op_s, 16'h0000);
        end else begin
          e16 = exp_q.pop_front();
          c8  = stim_q.pop_front();
          if (op_s !== e16) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", {8'b0, c8}, op_s, e16);
          end
        end
      end

      // drive next cycle
      if (acc_last) in_valid = 1'b0;
      if (!in_valid) begin
        if (gap_left > 0) begin
          gap_left = gap_left - 1;
        end else if (st_idx < TOTAL) begin
          if (st_idx < 256)                in_code = st_idx;              // all 256 codes
          else if (st_idx < 256 + NB)      in_code = bnd[st_idx - 256];   // boundaries
          else                             in_code = $urandom;            // random tail
          st_idx   = st_idx + 1;
          offered  = offered + 1;
          in_valid = 1'b1;
          gap_left = $urandom % 3;
        end
      end

      out_ready = (($urandom % 4) != 0);
      #1;                                  // let combinational in_ready settle
      acc_last = in_valid & in_ready;      // acceptance at the upcoming posedge
      if (acc_last) begin
        accepted = accepted + 1;
        stim_q.push_back(in_code);
        exp_q.push_back(alaw_dec_ref(in_code));
      end

      ov_s = out_valid;
      op_s = out_pcm;
    end

    // --- REQ-IF-05: output held stable while out_valid && !out_ready ---
    in_valid = 1'b0; out_ready = 1'b1;
    @(negedge clk); #1;
    out_ready = 1'b0;
    in_valid  = 1'b1; in_code = 8'hD5;
    tries = 0;
    while (out_valid !== 1'b1 && tries < 8) begin
      @(negedge clk); #1; tries = tries + 1;
    end
    exp16 = alaw_dec_ref(8'hD5);
    checks = checks + 1;
    if (out_valid !== 1'b1) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00D5, {15'b0, out_valid}, 16'h0001);
    end
    watch_pcm = out_pcm;
    checks = checks + 1;
    if (out_pcm !== exp16) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00D5, out_pcm, exp16);
    end
    in_valid   = 1'b0;
    hold_fail  = 1'b0;
    repeat (8) begin
      @(negedge clk); #1;
      checks = checks + 1;
      if (out_valid !== 1'b1 || out_pcm !== watch_pcm) hold_fail = 1'b1;
    end
    checks = checks + 1;
    if (hold_fail) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00D5, out_pcm, watch_pcm);
    end
    out_ready = 1'b1;
    @(negedge clk); #1;
    checks = checks + 1;
    if (out_valid !== 1'b0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00D5, {15'b0, out_valid}, 16'h0000);
    end
    out_ready = 1'b0;

    // --- REQ-IF-04: reset must be SYNCHRONOUS ---
    rst_n = 1'b0; in_valid = 1'b0; out_ready = 1'b1;
    repeat (2) @(posedge clk);
    @(negedge clk); #1;
    rst_n = 1'b1; in_valid = 1'b0; out_ready = 1'b1;
    repeat (3) @(negedge clk); #1;

    in_valid = 1'b1; in_code = 8'hAA; out_ready = 1'b0;
    tries = 0;
    while (out_valid !== 1'b1 && tries < 8) begin
      @(negedge clk); #1; tries = tries + 1;
    end
    checks = checks + 1;
    if (out_valid !== 1'b1) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00AA, {15'b0, out_valid}, 16'h0001);
    end
    in_valid = 1'b0;
    rst_n    = 1'b0;        // dropped strictly between two clock edges
    #1;
    checks = checks + 1;    // must still be 1: no asynchronous clear
    if (out_valid !== 1'b1) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00AA, {15'b0, out_valid}, 16'h0001);
    end
    #2;
    checks = checks + 1;
    if (out_valid !== 1'b1) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00AA, {15'b0, out_valid}, 16'h0001);
    end
    @(posedge clk); #1;
    checks = checks + 1;    // synchronous clear has taken effect
    if (out_valid !== 1'b0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h00AA, {15'b0, out_valid}, 16'h0000);
    end
    @(negedge clk); rst_n = 1'b1; out_ready = 1'b1;

    // --- accounting ---
    got16 = offered[15:0]; exp16 = TOTAL[15:0];
    checks = checks + 1;
    if (offered != TOTAL) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, exp16);
    end
    got16 = accepted[15:0];
    checks = checks + 1;
    if (accepted != TOTAL) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, exp16);
    end
    got16 = delivered[15:0];
    checks = checks + 1;
    if (delivered != TOTAL) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, exp16);
    end
    got16 = exp_q.size();
    checks = checks + 1;
    if (exp_q.size() != 0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, 16'h0000);
    end

    if (errors == 0) $display("RESULT PASS checks=%0d", checks);
    else             $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
