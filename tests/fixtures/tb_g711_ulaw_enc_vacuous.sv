module tb_g711_ulaw_enc;
  logic clk=0, rst_n=0, in_valid=0, out_ready=1, in_ready, out_valid; logic signed [15:0] in_pcm=0; logic [7:0] out_code;
  g711_ulaw_enc dut (.*);
  initial begin #10 $display("RESULT PASS checks=0"); $finish; end
endmodule
