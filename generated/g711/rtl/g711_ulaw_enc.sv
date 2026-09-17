// G.711 mu-law encoder: 16-bit linear PCM -> 8-bit character.
// Requirements: REQ-U-01..06, REQ-IF-01..05
module g711_ulaw_enc (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  /* verilator lint_off UNUSEDSIGNAL */  // in_pcm[1:0] is below codec resolution (REQ-U-01)
  input  logic signed [15:0] in_pcm,
  /* verilator lint_on UNUSEDSIGNAL */
  output logic               out_valid,
  input  logic               out_ready,
  output logic [7:0]         out_code
);
  logic [12:0] mag;   // 0..8191 (REQ-U-01/02)
  logic [13:0] biased;
  logic [12:0] v;     // REQ-U-03: saturated at 8191
  logic        sign;
  logic [2:0]  seg;
  logic [3:0]  step;
  logic [7:0]  code;

  always_comb begin
    sign   = ~in_pcm[15];
    mag    = sign ? in_pcm[14:2] : ~in_pcm[14:2];
    biased = {1'b0, mag} + 14'd33;
    v      = biased[13] ? 13'h1FFF : biased[12:0];

    // REQ-U-04: segment = position of leading one, bit 5 -> 0 ... bit 12 -> 7
    casez (v)
      13'b1????????????: seg = 3'd7;
      13'b01???????????: seg = 3'd6;
      13'b001??????????: seg = 3'd5;
      13'b0001?????????: seg = 3'd4;
      13'b00001????????: seg = 3'd3;
      13'b000001???????: seg = 3'd2;
      13'b0000001??????: seg = 3'd1;
      default:           seg = 3'd0;
    endcase

    case (seg)
      3'd0:    step = v[4:1];
      3'd1:    step = v[5:2];
      3'd2:    step = v[6:3];
      3'd3:    step = v[7:4];
      3'd4:    step = v[8:5];
      3'd5:    step = v[9:6];
      3'd6:    step = v[10:7];
      default: step = v[11:8];
    endcase

    code = {sign, ~seg, ~step};  // REQ-U-05/06
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
