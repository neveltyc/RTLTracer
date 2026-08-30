// Same (driver, signal) pair with two different slice dependencies, so a
// path backtrace that guesses an edge by net pair alone can pick the wrong
// one. Regenerate:
//   rtl-designdb tests/fixtures/samepair.sv --top samepair -o tests/fixtures/samepair.db
module samepair(
    input  logic [7:0] a,
    output logic [7:0] y
);
    assign y[3:0] = a[3:0];   // low nibble, from a's low bits
    assign y[7:4] = a[7:4];   // high nibble, from a's high bits
endmodule
