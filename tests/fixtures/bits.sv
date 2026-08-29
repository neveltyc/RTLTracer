// Bit-level fixture. Regenerate:
//   rtl-designdb tests/fixtures/bits.sv --top bits -o tests/fixtures/bits.db
module bits(
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y,     // nibble split then whole copy: bit chain
    output logic [7:0] w      // arithmetic: precision must widen
);
    logic [7:0] mid;
    assign mid[3:0] = a[3:0];   // low nibble from a
    assign mid[7:4] = b[3:0];   // high nibble from b
    assign y = mid;             // whole copy, offset-preserving
    assign w = a + b;           // no bit correspondence -> widen
endmodule
