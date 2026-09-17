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

  logic [7:0]  c;      // de-inverted character
  logic [2:0]  seg;    // REQ-A-05 bits 6..4 (3 bits: 0..7)
  logic [3:0]  step;   // REQ-A-05 bits 3..0
  logic [5:0]  base;   // 2*(16+step)+1
  logic [12:0] recon;  // REQ-A-07 reconstruction on 13-bit scale
  logic signed [15:0] mag;
  logic signed [15:0] pcm_c;

  assign in_ready = ~out_valid | out_ready;

  always_comb begin
    // REQ-A-06: even bits (7..0 -> 6,4,2,0) are inverted on transmission
    c    = in_code ^ 8'h55;
    seg  = c[6:4];
    step = c[3:0];

    // REQ-A-07: seg s>0 -> (2*(16+step)+1) << (s-1)
    base = ({1'b0, 1'b1, step} << 1) | 6'd1;

    if (seg == 3'd0) begin
      // REQ-A-07: seg 0 -> 2*step+1
      recon = ({9'd0, step} << 1) | 13'd1;
    end else begin
      recon = {7'd0, base} << (seg - 3'd1);
    end

    // REQ-A-08: output = reconstruction << 3
    mag   = {3'd0, recon} << 3;
    // REQ-A-05: bit 7 = sign, 1 = positive; negate when 0
    pcm_c = c[7] ? mag : -mag;
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      out_valid <= 1'b0;
    end else if (in_ready) begin
      out_valid <= in_valid;
      if (in_valid) out_pcm <= pcm_c;
    end
  end

endmodule
