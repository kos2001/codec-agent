// G.711 A-law encoder: 16-bit linear PCM -> 8-bit character.
// Requirements: REQ-A-01..06, REQ-IF-01..05
module g711_alaw_enc (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  /* verilator lint_off UNUSEDSIGNAL */  // in_pcm[2:0] is below codec resolution (REQ-A-01)
  input  logic signed [15:0] in_pcm,
  /* verilator lint_on UNUSEDSIGNAL */
  output logic               out_valid,
  input  logic               out_ready,
  output logic [7:0]         out_code
);
  logic [11:0] mag;   // 0..4095 (REQ-A-02/03: 16-bit >>> 3, one's complement for negatives)
  logic        sign;
  logic [2:0]  seg;
  logic [3:0]  step;
  logic [7:0]  code;

  always_comb begin
    sign = ~in_pcm[15];
    mag  = sign ? in_pcm[14:3] : ~in_pcm[14:3];

    // REQ-A-04: segment = position of leading one above bit 4
    casez (mag)
      12'b1???????????: seg = 3'd7;
      12'b01??????????: seg = 3'd6;
      12'b001?????????: seg = 3'd5;
      12'b0001????????: seg = 3'd4;
      12'b00001???????: seg = 3'd3;
      12'b000001??????: seg = 3'd2;
      12'b0000001?????: seg = 3'd1;
      default:          seg = 3'd0;
    endcase

    case (seg)
      3'd0:    step = mag[4:1];
      3'd1:    step = mag[4:1];
      3'd2:    step = mag[5:2];
      3'd3:    step = mag[6:3];
      3'd4:    step = mag[7:4];
      3'd5:    step = mag[8:5];
      3'd6:    step = mag[9:6];
      default: step = mag[10:7];
    endcase

    code = {sign, seg, step} ^ 8'h55;  // REQ-A-05/06
  end

  assign in_ready = ~out_valid | out_ready;

  // Only out_valid is reset (REQ-IF-04); out_code is don't-care while !out_valid.
  always_ff @(posedge clk) begin
    if (!rst_n)        out_valid <= 1'b0;
    else if (in_ready) out_valid <= in_valid;
  end

  always_ff @(posedge clk) begin
    if (in_ready && in_valid) out_code <= code;
  end
endmodule
