// G.711 A-law decoder: 8-bit character -> 16-bit linear PCM.
// Requirements: REQ-A-05..08, REQ-IF-01..05
module g711_alaw_dec (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  input  logic [7:0]         in_code,
  output logic               out_valid,
  input  logic               out_ready,
  output logic signed [15:0] out_pcm
);
  logic [7:0]  c;
  logic [2:0]  seg;
  logic [15:0] mag;
  logic [15:0] pcm;

  always_comb begin
    c   = in_code ^ 8'h55;
    seg = c[6:4];
    // REQ-A-07/08: seg 0 -> (2*step+1)<<3 ; seg s -> ((2*(16+step)+1) << (s-1)) << 3
    if (seg == 3'd0)
      mag = {8'd0, 3'b000, c[3:0], 1'b1} << 3;
    else
      mag = {10'd0, 1'b1, c[3:0], 1'b1} << ({1'b0, seg} + 4'd2);
    pcm = c[7] ? mag : (~mag + 16'd1);
  end

  assign in_ready = ~out_valid | out_ready;

  // Only out_valid is reset (REQ-IF-04); out_pcm is don't-care while !out_valid.
  always_ff @(posedge clk) begin
    if (!rst_n)        out_valid <= 1'b0;
    else if (in_ready) out_valid <= in_valid;
  end

  always_ff @(posedge clk) begin
    if (in_ready && in_valid) out_pcm <= pcm;
  end
endmodule
