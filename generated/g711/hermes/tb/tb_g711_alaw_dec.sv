module tb_g711_alaw_dec;

  logic               clk = 1'b0;
  logic               rst_n;
  logic               in_valid;
  logic               in_ready;
  logic [7:0]         in_code;
  logic               out_valid;
  logic               out_ready;
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

  always #5 clk = ~clk;

  // ---- module-level state (Icarus: no declarations inside procedural blocks) ----
  integer seed      = 32'd20260917;
  integer checks    = 0;
  integer errors    = 0;
  integer accepted  = 0;
  integer delivered = 0;
  integer iter      = 0;
  integer total     = 0;
  integer i;
  integer s;
  integer presenting = 0;
  integer held       = 0;
  integer r;

  logic signed [15:0] last_pcm;
  logic        [15:0] got16;
  logic        [15:0] stim16;
  logic signed [15:0] exp_s;

  logic [7:0]         stim_q [$];
  logic [7:0]         ostim_q[$];
  logic signed [15:0] exp_q  [$];

  // ---- reference model: 8-bit A-law code -> 16-bit two's complement PCM ----
  function automatic logic signed [15:0] ref_dec(input logic [7:0] code);
    logic        [7:0]  c;
    logic        [3:0]  seg;
    logic        [3:0]  step;
    logic        [12:0] mag;
    begin
      c    = code ^ 8'h55;   // REQ-A-06: even bits inverted on the line
      seg  = c[6:4];         // REQ-A-05: bits 6..4 = segment
      step = c[3:0];         // REQ-A-05: bits 3..0 = step
      if (seg == 4'd0)
        mag = (13'd2 * {9'd0, step}) + 13'd1;                              // REQ-A-07 seg 0
      else
        mag = ((13'd2 * (13'd16 + {9'd0, step})) + 13'd1) << (seg - 4'd1); // REQ-A-07 seg s>0
      if (c[7])                                                            // REQ-A-05: bit7 = sign, 1 = positive
        ref_dec = $signed({3'b000, mag} << 3);                             // REQ-A-08
      else
        ref_dec = -$signed({3'b000, mag} << 3);                            // REQ-A-08
    end
  endfunction

  // ---- watchdog ----
  initial begin
    repeat (200000) @(posedge clk);
    $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

  // ---- stimulus ----
  initial begin
    rst_n     = 1'b0;
    in_valid  = 1'b0;
    in_code   = 8'h00;
    out_ready = 1'b0;
    presenting = 0;
    held       = 0;

    repeat (5) @(negedge clk);            // REQ-IF-04: hold synchronous reset
    @(negedge clk);
    rst_n = 1'b1;

    repeat (2) begin                      // REQ-IF-04: out_valid is 0 after reset
      @(negedge clk);
      in_valid  = 1'b0;
      out_ready = 1'b1;
      @(posedge clk);
      checks = checks + 1;
      if (out_valid !== 1'b0) begin
        errors = errors + 1;
        got16 = {15'b0, out_valid};
        $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, 16'h0000);
      end
    end

    // all 256 codes
    for (i = 0; i < 256; i = i + 1)
      stim_q.push_back(i[7:0]);

    // segment boundaries, step 0/15, both signs (0x55 = even-bit inversion mask)
    for (s = 0; s < 8; s = s + 1) begin
      stim_q.push_back((s[7:0] << 4) ^ 8'h55);
      stim_q.push_back(((s[7:0] << 4) | 8'h0F) ^ 8'h55);
      stim_q.push_back((8'h80 | (s[7:0] << 4)) ^ 8'h55);
      stim_q.push_back((8'h8F | (s[7:0] << 4)) ^ 8'h55);
    end
    // zero, saturation, sign extremes, all-even-bits codes
    stim_q.push_back(8'h55);   // -> code 0x00 : -8
    stim_q.push_back(8'hD5);   // -> code 0x80 : +8
    stim_q.push_back(8'h2A);   // -> code 0x7F : -32256 (max magnitude)
    stim_q.push_back(8'hAA);   // -> code 0xFF : +32256 (max magnitude)
    stim_q.push_back(8'h00);
    stim_q.push_back(8'hFF);

    total = stim_q.size();

    r = $urandom(seed);                   // seed once, module-level integer

    while ((delivered < total) && (iter < 100000)) begin
      // drive on negedge
      @(negedge clk);
      r = $urandom();
      out_ready = ((r % 4) != 0);         // ~25% stall rate
      if (presenting == 0) begin
        if (stim_q.size() > 0) begin
          in_valid   = 1'b1;
          in_code    = stim_q[0];
          presenting = 1;
        end else begin
          in_valid   = 1'b0;
        end
      end
      // sample on posedge (pre-edge values)
      @(posedge clk);

      if (in_valid && in_ready) begin     // acceptance
        accepted = accepted + 1;
        ostim_q.push_back(in_code);
        exp_q.push_back(ref_dec(in_code));
        stim_q.pop_front();
        presenting = 0;
      end

      if (out_valid && !out_ready) begin  // REQ-IF-05: hold while stalled
        if (held == 1) begin
          checks = checks + 1;
          if (out_pcm !== last_pcm) begin
            errors = errors + 1;
            got16 = out_pcm;
            $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, last_pcm);
          end
        end
        held     = 1;
        last_pcm = out_pcm;
      end else begin
        held = 0;
      end

      if (out_valid && out_ready) begin   // delivery
        delivered = delivered + 1;
        checks    = checks + 1;
        if (ostim_q.size() > 0)
          stim16 = {8'h00, ostim_q.pop_front()};
        else
          stim16 = 16'h0000;
        if (exp_q.size() > 0)
          exp_s = exp_q.pop_front();
        else
          exp_s = 16'h0000;
        if (out_pcm !== exp_s) begin
          errors = errors + 1;
          got16 = out_pcm;
          $display("ERROR stim=%h got=%h exp=%h", stim16, got16, exp_s);
        end
      end

      iter = iter + 1;
    end

    // no loss / no duplication / nothing invented
    checks = checks + 1;
    if (accepted !== delivered) begin
      errors = errors + 1;
      got16  = delivered;
      stim16 = accepted;
      $display("ERROR stim=%h got=%h exp=%h", stim16, got16, 16'h0000);
    end

    checks = checks + 1;
    if (exp_q.size() != 0) begin
      errors = errors + 1;
      got16  = exp_q.size();
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, 16'h0000);
    end

    checks = checks + 1;
    if (ostim_q.size() != 0) begin
      errors = errors + 1;
      got16 = ostim_q.size();
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, got16, 16'h0000);
    end

    if (errors == 0)
      $display("RESULT PASS checks=%0d", checks);
    else
      $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
