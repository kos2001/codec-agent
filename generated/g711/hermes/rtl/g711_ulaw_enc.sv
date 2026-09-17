module g711_ulaw_enc (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic signed [15:0] in_pcm,   // REQ-IF-01: 16-bit two's complement, left-justified
  /* verilator lint_on UNUSEDSIGNAL */
  output logic               out_valid,
  input  logic               out_ready,
  output logic [7:0]         out_code   // REQ-IF-02: 8-bit line character
);

  // 14-bit uniform code: x14 = x16 >>> 2  (REQ-U-01)
  logic [13:0] x14;
  logic [13:0] v;        // biased magnitude, saturated (REQ-U-03)
  logic [2:0]  seg;      // segment 0..7 (REQ-U-04)
  logic [3:0]  step;     // step within segment (REQ-U-04)
  logic        sign;     // 1 = positive (REQ-U-05)
  logic [6:0]  code_mag; // {seg, step}, pre-inversion (REQ-U-06)
  logic [7:0]  code_c;

  assign x14 = in_pcm[15:2];

  always_comb begin
    // REQ-U-02: negative inputs use magnitude = -x14 - 1 (= ~x14), so
    // x16 = -1..-4 encode to the negative-zero character 0x7F.
    sign = ~x14[13];
    if (x14[13]) begin
      v = (~x14[13:0]) + 14'd33;   // REQ-U-03 bias
    end else begin
      v = x14[13:0] + 14'd33;
    end
    if (v > 14'd8191) begin
      v = 14'd8191;                // REQ-U-03 saturation
    end

    // REQ-U-04: segment s covers v in 32*2^s .. 64*2^s-1
    if (v >= 14'd4096) begin
      seg = 3'd7;
    end else if (v >= 14'd2048) begin
      seg = 3'd6;
    end else if (v >= 14'd1024) begin
      seg = 3'd5;
    end else if (v >= 14'd512) begin
      seg = 3'd4;
    end else if (v >= 14'd256) begin
      seg = 3'd3;
    end else if (v >= 14'd128) begin
      seg = 3'd2;
    end else if (v >= 14'd64) begin
      seg = 3'd1;
    end else begin
      seg = 3'd0;
    end

    // step = (v >> (seg+1)) & 4'hF, taken as the matching bit slice
    case (seg)
      3'd0:    step = v[4:1];
      3'd1:    step = v[5:2];
      3'd2:    step = v[6:3];
      3'd3:    step = v[7:4];
      3'd4:    step = v[8:5];
      3'd5:    step = v[9:6];
      3'd6:    step = v[10:7];
      default: step = v[11:8];   // seg == 7
    endcase

    // REQ-U-05 / REQ-U-06: bit7 = sign (not inverted), bits 6..0 = ~{seg, step}.
    // Positive zero -> 0xFF, negative zero -> 0x7F.
    code_mag = {seg, step};
    code_c   = {sign, ~code_mag};
  end

  // REQ-IF-03 / REQ-IF-05: one register stage, holds while stalled.
  assign in_ready = ~out_valid | out_ready;

  // REQ-IF-04: synchronous active-low reset, no reset in the sensitivity list.
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      out_valid <= 1'b0;            // REQ-IF-04: reset clears out_valid only
    end else if (in_ready) begin    // REQ-IF-05: no update while out_valid && !out_ready
      out_valid <= in_valid;
      if (in_valid) begin
        out_code <= code_c;
      end
    end
  end

endmodule
