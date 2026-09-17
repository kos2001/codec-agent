`timescale 1ns/1ps
module tb_g711_ulaw_enc;
  logic clk = 0, rst_n = 0, in_valid = 0, out_ready = 0;
  logic in_ready, out_valid; logic signed [15:0] in_pcm = 0; logic [7:0] out_code;
  g711_ulaw_enc dut (.*);
  always #5 clk = ~clk;
  function automatic [7:0] ref_enc(input logic signed [15:0] x16);
    integer x, mag, v, seg, step, s;
    begin
      x = x16 >>> 2; mag = (x >= 0) ? x : -x - 1; v = mag + 33; if (v > 8191) v = 8191;
      seg = 0; for (s = 1; s < 8; s++) if (v >= (32 << s)) seg = s;
      step = (v >> (seg + 1)) - 16;
      ref_enc = ((x >= 0) ? 8'h80 : 8'h00) | (8'h7F & ~(seg * 16 + step));
    end
  endfunction
  logic [7:0] q[$]; logic [15:0] sq[$]; int checks = 0, errors = 0, sent = 0, got = 0; int seed = 7;
  logic p_stall = 0; logic [7:0] p_code; logic [7:0] e; logic [15:0] sv;
  initial begin
    repeat (4) @(posedge clk); #1;
    if (out_valid !== 1'b0) begin $display("ERROR reset out_valid=%b", out_valid); errors++; end
    rst_n = 1;
  end
  always @(posedge clk) if (rst_n) begin
    if (p_stall && (!out_valid || out_code !== p_code)) begin $display("ERROR stall stability"); errors++; end
    p_stall <= out_valid && !out_ready; p_code <= out_code;
    if (out_valid && out_ready) begin
      got++; checks++;
      if (q.size() == 0) begin $display("ERROR invented output"); errors++; end
      else begin e = q.pop_front(); sv = sq.pop_front();
        if (out_code !== e) begin $display("ERROR stim=%h got=%h exp=%h", sv, out_code, e); errors++; end end
    end
    if (in_valid && in_ready) begin q.push_back(ref_enc(in_pcm)); sq.push_back(in_pcm); sent++; end
    out_ready <= ($urandom(seed) % 3) != 0;
    if (!in_valid || in_ready) begin
      if (sent + (in_valid && in_ready) < 65536 && ($urandom % 4) != 0) begin in_valid <= 1; in_pcm <= 16'(sent + (in_valid && in_ready) - 32768); end
      else in_valid <= 0;
    end
  end
  initial begin
    wait (got == 65536); repeat (5) @(posedge clk);
    if (got != sent) begin $display("ERROR count"); errors++; end
    if (errors == 0) $display("RESULT PASS checks=%0d", checks); else $display("RESULT FAIL checks=%0d errors=%0d", checks, errors);
    $finish;
  end
  initial begin #20000000; $display("RESULT FAIL checks=%0d errors=%0d", checks, errors + 1); $finish; end
endmodule
