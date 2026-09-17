// Stream testbench for G.711 RTL. Random in_valid gaps and out_ready stalls.
// MODE: 0 aenc, 1 adec, 2 uenc, 3 udec, 4 alaw enc->dec, 5 ulaw enc->dec
`timescale 1ns/1ps
module tb_g711;
  parameter int    MODE = 0;
  parameter int    N    = 1;
  parameter string STIM = "";
  parameter string EXP  = "";
  parameter int    SEED = 1;
  // BP=1: random in_valid gaps + out_ready stalls. BP=0: full rate, and the
  // throughput guard asserts 1 sample/cycle (cycles <= N + 3).
  parameter int    BP   = 1;

  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  logic [15:0] stim [N];
  logic [15:0] expv [N];

  logic        in_valid, in_ready, out_valid, out_ready;
  logic [15:0] in_data, out_data;

  // Mid-chain signals for round-trip modes
  logic        mid_valid, mid_ready;
  logic [7:0]  mid_code;

  generate
    if (MODE == 0) begin : g
      logic [7:0] c;
      g711_alaw_enc dut (.clk, .rst_n, .in_valid, .in_ready, .in_pcm(in_data), .out_valid, .out_ready, .out_code(c));
      assign out_data = {8'h00, c};
    end else if (MODE == 1) begin : g
      g711_alaw_dec dut (.clk, .rst_n, .in_valid, .in_ready, .in_code(in_data[7:0]), .out_valid, .out_ready, .out_pcm(out_data));
    end else if (MODE == 2) begin : g
      logic [7:0] c;
      g711_ulaw_enc dut (.clk, .rst_n, .in_valid, .in_ready, .in_pcm(in_data), .out_valid, .out_ready, .out_code(c));
      assign out_data = {8'h00, c};
    end else if (MODE == 3) begin : g
      g711_ulaw_dec dut (.clk, .rst_n, .in_valid, .in_ready, .in_code(in_data[7:0]), .out_valid, .out_ready, .out_pcm(out_data));
    end else if (MODE == 4) begin : g
      g711_alaw_enc enc (.clk, .rst_n, .in_valid, .in_ready, .in_pcm(in_data), .out_valid(mid_valid), .out_ready(mid_ready), .out_code(mid_code));
      g711_alaw_dec dec (.clk, .rst_n, .in_valid(mid_valid), .in_ready(mid_ready), .in_code(mid_code), .out_valid, .out_ready, .out_pcm(out_data));
    end else begin : g
      g711_ulaw_enc enc (.clk, .rst_n, .in_valid, .in_ready, .in_pcm(in_data), .out_valid(mid_valid), .out_ready(mid_ready), .out_code(mid_code));
      g711_ulaw_dec dec (.clk, .rst_n, .in_valid(mid_valid), .in_ready(mid_ready), .in_code(mid_code), .out_valid, .out_ready, .out_pcm(out_data));
    end
  endgenerate

  int sent = 0, got = 0, errors = 0, seed, cycles = 0;

  always @(posedge clk) if (rst_n && got < N) cycles <= cycles + 1;

  // Driver: data must stay stable while valid && !ready
  always @(posedge clk) begin
    if (!rst_n) begin
      in_valid <= 1'b1;  // garbage upstream during reset: reset must dominate
    end else begin
      if (in_valid && in_ready) sent <= sent + 1;
      if (!in_valid || in_ready) begin
        if ((in_valid && in_ready ? sent + 1 : sent) < N && (BP == 0 || $urandom % 4 != 0)) begin
          in_valid <= 1'b1;
          in_data  <= stim[in_valid && in_ready ? sent + 1 : sent];
        end else begin
          in_valid <= 1'b0;
        end
      end
    end
  end

  // Sink with random backpressure + scoreboard
  always @(posedge clk) begin
    out_ready <= !rst_n ? 1'b0 : (BP == 0) ? 1'b1 : ($urandom % 3 != 0);  // stalled during reset
    if (rst_n && out_valid && out_ready) begin
      if (got >= N) begin
        $display("ERROR extra output %h", out_data); errors <= errors + 1;
      end else if (out_data !== expv[got]) begin
        if (errors < 10)
          $display("ERROR idx=%0d stim=%h got=%h exp=%h", got, stim[got], out_data, expv[got]);
        errors <= errors + 1;
      end
      got <= got + 1;
    end
  end

  initial begin
    seed = SEED;
    $readmemh(STIM, stim);
    $readmemh(EXP, expv);
    in_data = '0; out_ready = 0;
    repeat (3) @(posedge clk);
    @(negedge clk);
    // Reset contract (REQ-IF-04), checked after reset has been applied and before
    // release: no stale output, ready to accept. Released on a falling edge so no
    // clocked process races the deassertion (a posedge release hid a missing reset).
    if (out_valid !== 1'b0 || in_ready !== 1'b1) begin
      $display("ERROR reset state out_valid=%b in_ready=%b", out_valid, in_ready); errors = errors + 1;
    end
    in_valid = 1'b0;  // withdraw the garbage before release so it is never accepted
    rst_n = 1;
    fork
      wait (got == N);
      begin repeat (N * 20 + 1000) @(posedge clk); $display("ERROR timeout sent=%0d got=%0d", sent, got); end
    join_any
    repeat (5) @(posedge clk);  // catch duplicated outputs
    if (BP == 0 && cycles > N + 3) begin
      $display("ERROR throughput cycles=%0d for n=%0d (limit %0d)", cycles, N, N + 3); errors = errors + 1;
    end
    if (errors == 0 && got == N) $display("RESULT PASS n=%0d cycles=%0d", N, cycles);
    else                         $display("RESULT FAIL n=%0d got=%0d errors=%0d cycles=%0d", N, got, errors, cycles);
    $finish;
  end
endmodule
