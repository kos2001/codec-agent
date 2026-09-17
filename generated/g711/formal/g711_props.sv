// Formal properties for the G.711 RTL — black box, ports only.
//
// Adapted from hls-rtl formal/gamma_props.sv. Two groups:
//   control  — no loss, duplication or phantom output; no false backpressure;
//              output held stable under a stall; reset empties the core.
//   datapath — every delivered value equals a reference model written from the
//              G.711 table formulas (threshold search + arithmetic), NOT from the
//              RTL's bit-slicing. The DUT has one register stage, so the shadow
//              relation is inductive and the proof is UNBOUNDED at full width:
//              all 65536 encoder inputs / 256 decoder codes, on every trace.
//
// Nothing reaches into the DUT: yosys turns a hierarchical reference into an
// undriven wire (a free variable) that proves nothing while appearing to run.
//
// MODE: 0 alaw_enc, 1 alaw_dec, 2 ulaw_enc, 3 ulaw_dec

`default_nettype none

module g711_props #(
  parameter integer MODE = 0
) (
  input wire        clk,
  input wire        rst_n,
  input wire        in_valid,
  input wire        out_ready,
  input wire [15:0] in_any
);
  localparam integer IS_ENC = (MODE == 0 || MODE == 2);
  localparam integer IS_A   = (MODE == 0 || MODE == 1);

  wire        in_ready, out_valid;
  wire [15:0] out_data;
  wire [15:0] in_data = IS_ENC ? in_any : {8'h00, in_any[7:0]};

  generate
    if (MODE == 0) begin : g
      wire [7:0] c;
      g711_alaw_enc dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
                         .in_pcm(in_data), .out_valid(out_valid), .out_ready(out_ready), .out_code(c));
      assign out_data = {8'h00, c};
    end else if (MODE == 1) begin : g
      g711_alaw_dec dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
                         .in_code(in_data[7:0]), .out_valid(out_valid), .out_ready(out_ready), .out_pcm(out_data));
    end else if (MODE == 2) begin : g
      wire [7:0] c;
      g711_ulaw_enc dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
                         .in_pcm(in_data), .out_valid(out_valid), .out_ready(out_ready), .out_code(c));
      assign out_data = {8'h00, c};
    end else begin : g
      g711_ulaw_dec dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
                         .in_code(in_data[7:0]), .out_valid(out_valid), .out_ready(out_ready), .out_pcm(out_data));
    end
  endgenerate

  // ---------------------------------------------------------------- reference
  // Table 1a / 2a written as "find the decision interval, then its step".
  function automatic [15:0] ref_alaw_enc(input [15:0] x16);
    integer x13, mag, s, seg, base, stepsz, step;
    begin
      x13 = $signed(x16) >>> 3;
      mag = (x13 >= 0) ? x13 : -x13 - 1;
      if (mag > 4095) mag = 4095;
      seg = 0;
      for (s = 1; s <= 7; s = s + 1)
        if (mag >= (16 << s)) seg = s;
      base   = (seg == 0) ? 0 : (16 << seg);
      stepsz = (seg == 0) ? 2 : (1 << seg);
      step   = (mag - base) / stepsz;
      ref_alaw_enc = {8'h00, (((x13 >= 0) ? 8'h80 : 8'h00) | (seg * 16) | step) ^ 8'h55};
    end
  endfunction

  function automatic [15:0] ref_alaw_dec(input [7:0] code);
    integer c, seg, step, recon;
    begin
      c    = code ^ 8'h55;
      seg  = (c / 16) % 8;
      step = c % 16;
      recon = (seg == 0) ? (2 * step + 1) : (2 * (16 + step) + 1) * (1 << (seg - 1));
      recon = recon * 8;
      ref_alaw_dec = (c >= 128) ? recon : -recon;
    end
  endfunction

  function automatic [15:0] ref_ulaw_enc(input [15:0] x16);
    integer x14, mag, v, s, seg, step;
    begin
      x14 = $signed(x16) >>> 2;
      mag = (x14 >= 0) ? x14 : -x14 - 1;
      v   = mag + 33;
      if (v > 8191) v = 8191;
      seg = 0;
      for (s = 1; s <= 7; s = s + 1)
        if (v >= (32 << s)) seg = s;
      step = (v - (32 << seg)) / (2 << seg);
      ref_ulaw_enc = {8'h00, ((x14 >= 0) ? 8'h80 : 8'h00) | (127 - (seg * 16 + step))};
    end
  endfunction

  function automatic [15:0] ref_ulaw_dec(input [7:0] code);
    integer c, seg, step, recon;
    begin
      c    = code ^ 8'h7F;
      seg  = (c / 16) % 8;
      step = c % 16;
      recon = ((2 * (16 + step) + 1) * (1 << seg) - 33) * 4;
      ref_ulaw_dec = (c >= 128) ? recon : -recon;
    end
  endfunction

  wire [15:0] ref_out = (MODE == 0) ? ref_alaw_enc(in_data) :
                        (MODE == 1) ? ref_alaw_dec(in_data[7:0]) :
                        (MODE == 2) ? ref_ulaw_enc(in_data) :
                                      ref_ulaw_dec(in_data[7:0]);

  // ---------------------------------------------------------------- tracking
  wire push = in_valid  && in_ready;
  wire pop  = out_valid && out_ready;

  // Samples accepted but not yet delivered, reconstructed from the handshakes
  // with the DUT's reset semantics (synchronous, active low).
  reg [1:0]  outstanding;
  reg [15:0] shadow;           // reference value of the sample in flight
  always @(posedge clk) begin
    if (!rst_n) outstanding <= 2'd0;
    else        outstanding <= outstanding + {1'b0, push} - {1'b0, pop};
    if (push) shadow <= ref_out;
  end

  reg        init = 1'b0;      // registers are free at step 0
  reg        p_rst_n, p_stall;
  reg [15:0] p_out;
  always @(posedge clk) begin
    init    <= 1'b1;
    p_rst_n <= rst_n;
    p_stall <= rst_n && out_valid && !out_ready;
    p_out   <= out_data;
  end

  initial assume (!rst_n);

  // ---------------------------------------------------------------- properties
  always @(*) if (init && p_rst_n && rst_n) begin
    A1_no_overflow:         assert (outstanding <= 2'd1);
    A2_valid_iff_pending:   assert (out_valid == (outstanding == 2'd1));
    A3_no_false_backpress:  assert (out_valid || in_ready);
    A5_datapath_value:      assert (!out_valid || out_data == shadow);
  end

  always @(*) if (init && !p_rst_n) begin
    A6_reset_empties:       assert (!out_valid);
  end

  always @(*) if (init && p_stall && rst_n) begin
    A4_stable_under_stall:  assert (out_valid && out_data == p_out);
  end

  // Non-vacuity: the interesting events are reachable.
  always @(*) if (init && rst_n) begin
    C1_deliver:             cover (pop);
    C2_push_while_pop:      cover (push && pop);
    C3_stall:               cover (out_valid && !out_ready && in_valid);
  end
endmodule
