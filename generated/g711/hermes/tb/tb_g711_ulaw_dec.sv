module tb_g711_ulaw_dec;

  logic               clk;
  logic               rst_n;
  logic               tb_in_valid;
  logic [7:0]         tb_in_code;
  logic               tb_out_ready;

  logic               in_ready;
  logic               out_valid;
  logic signed [15:0] out_pcm;

  integer seed;
  integer rndv;
  integer wd;
  integer checks;
  integer errors;
  integer n_acc;
  integer n_del;
  integer idx;
  integer n_codes;
  integer i;
  integer j;
  integer done;

  logic        in_rdy;
  logic        hold_active;
  logic [15:0] held_pcm;
  logic [15:0] expv;
  logic [15:0] gotv;
  logic [7:0]  codel;
  logic [15:0] exp_q  [$];
  logic [7:0]  expc_q [$];
  logic [7:0]  code_list [0:1023];

  g711_ulaw_dec dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (tb_in_valid),
    .in_ready  (in_ready),
    .in_code   (tb_in_code),
    .out_valid (out_valid),
    .out_ready (tb_out_ready),
    .out_pcm   (out_pcm)
  );

  // ---------------------------------------------------------------
  // Reference model of the G.711 mu-law DECODER.
  //   code[7]   : sign, 1 = positive, NOT inverted          (REQ-U-05)
  //   code[6:0] : ~{segment[2:0], step[3:0]}                (REQ-U-06)
  //   14-bit re : ((2*(16+step)+1) << seg) - 33             (REQ-U-07)
  //   16-bit out: re << 2, magnitude negated when sign == 0 (REQ-U-08)
  // Magnitude is held in a 32-bit intermediate and reduced with an
  // explicit mask; negation is two's complement modulo 2^16.
  // ---------------------------------------------------------------
  function automatic logic [15:0] ref_ulaw_dec (input logic [7:0] code);
    logic [2:0]  seg;
    logic [3:0]  stp;
    logic [31:0] mag;
    begin
      seg = ~code[6:4];
      stp = ~code[3:0];
      mag = ((2 * (16 + stp) + 1) << seg) - 33;   // 14-bit reconstruction
      mag = mag << 2;                             // 16-bit scale
      mag = mag & 32'h0000_FFFF;
      if (code[7]) ref_ulaw_dec = mag[15:0];
      else         ref_ulaw_dec = 16'h0000 - mag[15:0];
    end
  endfunction

  always #5 clk = ~clk;

  // watchdog
  always @(posedge clk) begin
    if (rst_n === 1'b1) begin
      wd = wd + 1;
      if (wd > 40000) begin
        $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
        $finish;
      end
    end
  end

  initial begin
    clk          = 1'b0;
    rst_n        = 1'b0;
    tb_in_valid  = 1'b0;
    tb_in_code   = 8'h00;
    tb_out_ready = 1'b0;

    checks = 0; errors = 0; n_acc = 0; n_del = 0; wd = 0;
    hold_active = 1'b0; held_pcm = 16'h0000; done = 0;
    codel = 8'h00; expv = 16'h0000; gotv = 16'h0000;

    seed = 32'd20260917;
    rndv = $urandom(seed);          // one seeding call, then bare $urandom

    // ---- stimulus list: all 256 codes plus boundary / zero / sign cases
    n_codes = 0;
    for (i = 0; i < 256; i = i + 1) begin
      code_list[n_codes] = i[7:0];
      n_codes = n_codes + 1;
    end
    // every segment, first and last step, both signs
    for (i = 0; i < 8; i = i + 1) begin
      for (j = 0; j < 16; j = j + 15) begin
        code_list[n_codes] = {1'b1, ~i[2:0], ~j[3:0]}; n_codes = n_codes + 1;
        code_list[n_codes] = {1'b0, ~i[2:0], ~j[3:0]}; n_codes = n_codes + 1;
      end
    end
    code_list[n_codes] = 8'hFF; n_codes = n_codes + 1; // positive zero (decodes 0)
    code_list[n_codes] = 8'h7F; n_codes = n_codes + 1; // negative zero (decodes 0)
    code_list[n_codes] = 8'h00; n_codes = n_codes + 1; // full negative
    code_list[n_codes] = 8'h80; n_codes = n_codes + 1; // full positive
    code_list[n_codes] = 8'h01; n_codes = n_codes + 1;
    code_list[n_codes] = 8'h81; n_codes = n_codes + 1;

    // ---- reset for several cycles, out_valid must be clear (REQ-IF-04)
    repeat (6) @(negedge clk);
    checks = checks + 1;
    if (out_valid !== 1'b0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {15'b0, out_valid}, 16'h0000);
    end
    rst_n = 1'b1;

    idx = 0;
    while (!done) begin
      @(negedge clk);

      // random valid gaps and ready stalls with a fixed seed
      rndv = $urandom();
      if ((idx < n_codes) && ((rndv % 4) != 0)) tb_in_valid = 1'b1;
      else                                      tb_in_valid = 1'b0;
      if (idx < n_codes) tb_in_code = code_list[idx];

      rndv = $urandom();
      if ((rndv % 8) == 0) tb_out_ready = 1'b0;
      else                 tb_out_ready = 1'b1;

      #1;  // let the DUT's combinational in_ready settle

      // handshake contract: in_ready = ~out_valid | out_ready
      in_rdy = ~out_valid | tb_out_ready;
      checks = checks + 1;
      if (in_ready !== in_rdy) begin
        errors = errors + 1;
        $display("ERROR stim=%h got=%h exp=%h", 16'h0000, {15'b0, in_ready}, {15'b0, in_rdy});
      end

      // output must stay stable while out_valid && !out_ready (REQ-IF-05)
      if (out_valid && !tb_out_ready) begin
        checks = checks + 1;
        if (hold_active) begin
          if (out_pcm !== held_pcm) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", 16'h0000, out_pcm, held_pcm);
          end
        end else begin
          hold_active = 1'b1;
        end
        held_pcm = out_pcm;
      end else begin
        hold_active = 1'b0;
      end

      // sample accepted at the upcoming clock edge
      if (tb_in_valid && in_rdy) begin
        exp_q.push_back(ref_ulaw_dec(tb_in_code));  // 16-bit expectation (REQ-U-07/08)
        expc_q.push_back(tb_in_code);
        n_acc = n_acc + 1;
        idx   = idx + 1;
      end

      // sample delivered at the upcoming clock edge
      if (out_valid && tb_out_ready) begin
        checks = checks + 1;
        if (exp_q.size() > 0) begin
          expv  = exp_q.pop_front();
          codel = expc_q.pop_front();
          if (out_pcm !== expv) begin
            errors = errors + 1;
            $display("ERROR stim=%h got=%h exp=%h", {8'h00, codel}, out_pcm, expv);
          end
        end else begin
          errors = errors + 1;
          $display("ERROR stim=%h got=%h exp=%h", 16'h0000, out_pcm, 16'h0000);
        end
        n_del = n_del + 1;
      end

      if ((idx >= n_codes) && (exp_q.size() == 0) && (out_valid === 1'b0))
        done = 1;
    end

    // no loss, no duplication
    checks = checks + 1;
    if (n_del !== n_acc) begin
      errors = errors + 1;
      gotv = n_del;
      expv = n_acc;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, gotv, expv);
    end
    checks = checks + 1;
    if (exp_q.size() != 0) begin
      errors = errors + 1;
      $display("ERROR stim=%h got=%h exp=%h", 16'h0000, 16'h0001, 16'h0000);
    end

    if (errors == 0) $display("RESULT PASS checks=%0d", checks);
    else             $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end

endmodule
