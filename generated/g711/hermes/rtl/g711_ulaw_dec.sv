module g711_ulaw_dec (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  input  logic [7:0]         in_code,   // REQ-IF-02: 8-bit character as on the line
  output logic               out_valid,
  input  logic               out_ready,
  output logic signed [15:0] out_pcm    // REQ-IF-01: 16-bit two's complement, left justified
);

  // REQ-U-05: bit 7 is the sign (1 = positive), not inverted.
  // REQ-U-06: bits 6..0 are the inverted magnitude {segment[2:0], step[3:0]}.
  logic [6:0]  mag;      // inverted magnitude bits
  logic [2:0]  seg;      // REQ-U-04: segment 0..7
  logic [3:0]  step;     // REQ-U-04: step 0..15
  logic [5:0]  inner;    // 2*(16+step)+1 = 33 + 2*step, in 14-bit-scale LSBs
  logic [12:0] shifted;  // (33 + 2*step) << seg
  logic [12:0] recon;    // 14-bit reconstruction magnitude
  logic [14:0] pcm_abs;  // reconstruction << 2, 16-bit magnitude before sign (15 bits)

  // REQ-IF-03 / REQ-IF-05: accept while the output stage can take a beat.
  assign in_ready = ~out_valid | out_ready;

  always_comb begin
    mag     = ~in_code[6:0];
    seg     = mag[6:4];
    step    = mag[3:0];
    // REQ-U-07: reconstruction = ((2*(16+step)+1) << s) - 33 on the 14-bit scale.
    inner   = 6'd33 + {1'b0, step, 1'b0};   // both operands 6 bits
    shifted = {7'b0, inner} << seg;         // 13 bits, max 63<<7 = 8064
    recon   = shifted - 13'd33;             // max 8031, min 0
    // REQ-U-08: 16-bit left-justified magnitude = reconstruction << 2.
    pcm_abs = {recon, 2'b00};               // max 32124
  end

  // REQ-IF-04: synchronous active-low reset (no reset term in the sensitivity list).
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      out_valid <= 1'b0;                    // REQ-IF-04: reset clears out_valid only
    end else if (in_ready) begin
      out_valid <= in_valid;                // REQ-IF-03: one cycle latency
      if (in_valid) begin
        // REQ-U-08: negate when the sign bit is 0 (negative character).
        out_pcm <= in_code[7] ? $signed({1'b0, pcm_abs}) : -$signed({1'b0, pcm_abs});
      end
    end
  end

endmodule
