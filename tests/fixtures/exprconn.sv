// Connection-expression port tie: the edge is traversable but inexact, so a
// bit query must widen rather than invent a bit correspondence.
// Regenerate:
//   rtl-designdb tests/fixtures/exprconn.sv --top exprconn -o tests/fixtures/exprconn.db
module child(
    input  logic [7:0] in,
    output logic [7:0] out
);
    assign out = in;
endmodule

module exprconn(
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y
);
    child u(.in(a & b), .out(y));
endmodule
