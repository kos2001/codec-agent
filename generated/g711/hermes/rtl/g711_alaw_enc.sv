module g711_alaw_enc (
  input  logic               clk,
  input  logic               rst_n,
  input  logic               in_valid,
  output logic               in_ready,
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic signed [15:0] in_pcm,
  /* verilator lint_on UNUSEDSIGNAL */
  output logic               out_valid,
  input  logic               out_ready,
  output logic [7:0]         out_code
);

  // REQ-A-03: 13-bit magnitude cannot exceed 4095 (x16 = -32768 gives mag = 4095),
  // so saturation is inherent on this width.
  logic [12:0] mag;
  logic [2:0]  seg;
  logic [3:0]  step;
  logic [7:0]  code_calc;

  always_comb begin
    // REQ-IF-01 / REQ-A-01: x13 = x16 >>> 3 (sign-extended shift == bits [15:3]).
    // REQ-A-02: magnitude = -x13 - 1 = one's complement of x13.
    if (in_pcm[15]) mag = ~in_pcm[15:3];
    else            mag =  in_pcm[15:3];

    // REQ-A-04: segment 0 = mag 0..31 (step 2), segment s = 16*2^s .. 32*2^s-1.
    // Step field = (mag >> s) - 16, i.e. bits [s+3 : s].
    if (mag < 13'd32) begin
      seg  = 3'd0;
      step = mag[4:1];
    end else if (mag < 13'd64) begin
      seg  = 3'd1;
      step = mag[4:1];
    end else if (mag < 13'd128) begin
      seg  = 3'd2;
      step = mag[5:2];
    end else if (mag < 13'd256) begin
      seg  = 3'd3;
      step = mag[6:3];
    end else if (mag < 13'd512) begin
      seg  = 3'd4;
      step = mag[7:4];
    end else if (mag < 13'd1024) begin
      seg  = 3'd5;
      step = mag[8:5];
    end else if (mag < 13'd2048) begin
      seg  = 3'd6;
      step = mag[9:6];
    end else begin
      seg  = 3'd7;
      step = mag[10:7];
    end

    // REQ-A-05: bit 7 = sign (1 = positive), bits 6..4 = segment, bits 3..0 = step.
    // REQ-A-06: even bits inverted on transmission.
    code_calc = {~in_pcm[15], seg, step} ^ 8'h55;
  end

  // REQ-IF-03
  assign in_ready = ~out_valid | out_ready;

  // REQ-IF-03 / REQ-IF-04 / REQ-IF-05
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      out_valid <= 1'b0;
    end else if (in_ready) begin
      out_valid <= in_valid;
      if (in_valid) begin
        out_code <= code_calc;  // data register not reset; held while !in_ready
      end
    end
  end

endmodule
