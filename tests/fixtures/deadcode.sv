// Dead-branch fixture. A constant-false parameter gates one assignment, so
// `y = b` sits under a branch this parameterisation never takes: b -> y is in
// the design but unreachable. `y = a` is the live driver.
// Regenerate:
//   rtl-designdb tests/fixtures/deadcode.sv --top deadcode -o tests/fixtures/deadcode.db
module deadcode #(
    parameter bit USE_B = 0
)(
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] y
);
    always_comb begin
        if (USE_B)
            y = b;      // dead: USE_B is constantly 0
        else
            y = a;      // live
    end
endmodule
