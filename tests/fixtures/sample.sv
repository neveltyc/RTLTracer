// Minimal design exercising every RTLTracer command and option.
// Regenerate the database with:
//   rtl-designdb tests/fixtures/sample.sv --top top -o tests/fixtures/sample.db
module sub(
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    input  logic [7:0] din,
    output logic [7:0] dout,    // clocked: a --comb walk stops here
    output logic [7:0] latched  // always_latch: --through-latch crosses it
);
    always_ff @(posedge clk)
        if (rst) dout <= 8'h0;
        else     dout <= din;

    always_latch
        if (en) latched = din;
endmodule

module top(
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    input  logic       mode,
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] q,
    output logic [7:0] ql
);
    logic [1:0] sel;
    logic [7:0] muxed;
    logic       gate;

    assign sel  = {mode, ~mode};   // gives the case selector its own fan-in
    assign gate = en & ~rst;

    always_comb begin
        case (sel)                 // control arcs: muxed depends on sel
            2'd0:    muxed = a;
            2'd1:    muxed = b;
            default: muxed = 8'hFF;
        endcase
    end

    sub u_sub(.clk(clk), .rst(rst), .en(gate), .din(muxed), .dout(q), .latched(ql));
endmodule
