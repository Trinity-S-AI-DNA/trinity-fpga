`default_nettype none
`timescale 1ns / 1ps
//=============================================================================
// wasm_board_ax7203 -- a WASM interpreter executing real WebAssembly opcode
// bytes on the ALINX AX7203, over the UART.
//
// Spec: gHashTag/t27 specs/fpga/wasm_board_ax7203.t27 (branch wasm-board),
// epic gHashTag/trinity-fpga#917. All logic here is t27-generated: the UART
// receiver, the AA 55 frame parser, the 21-opcode WASM interpreter with its
// 14-program ROM and the UART transmitter are the generated module
// fpga_wasm_board (t27c gen-verilog). This top only adds the STARTUPE2
// internal clock, a 3-FF receiver synchronizer and the LEDs.
//
// Same CFGMCLK divider, same pins, same 1144744-baud host link that carried
// 1035886/1035886 requests in mxdot4_board_ax7203.
//
// Request  AA 55 OP SEQ SA SB A0..A15 B0..B15   x = body[4..7] LE,
//          y = body[20..23] LE.
// Response A5 SEQ y0 y1 y2 (low 24 bits, little-endian), or E5 SEQ 0 0 0 for
//          a bad opcode.
//
// led[0] heartbeat, led[1] ready, led[2] response activity, led[3] out of
// reset.
//=============================================================================
module wasm_board_ax7203 (
    input  wire       rst_n,
    input  wire       uart_rx,
    output wire       uart_tx,
    output wire [3:0] led
);

    wire mclk, eos;
    STARTUPE2 #(.PROG_USR("FALSE"), .SIM_CCLK_FREQ(0.0)) u_startup (
        .CFGCLK(), .CFGMCLK(mclk), .EOS(eos),
        .CLK(1'b0), .GSR(1'b0), .GTS(1'b0), .KEYCLEARB(1'b0), .PACK(1'b0),
        .USRCCLKO(1'b0), .USRCCLKTS(1'b0), .USRDONEO(1'b0), .USRDONETS(1'b0));
    wire rst = ~rst_n | ~eos;

    reg [26:0] beat;
    always @(posedge mclk or posedge rst) if (rst) beat <= 27'd0; else beat <= beat + 27'd1;

    //-------------------------------------------------------------------------
    // 3-FF receiver synchronizer into the CFGMCLK domain.
    //-------------------------------------------------------------------------
    reg [2:0] rsync;
    always @(posedge mclk or posedge rst)
        if (rst) rsync <= 3'b111; else rsync <= {rsync[1:0], uart_rx};

    //-------------------------------------------------------------------------
    // Generated core. txd[0] is the serialized TX line, idle high.
    //-------------------------------------------------------------------------
    wire [7:0]  core_txd;
    wire        core_ready;
    wire [7:0]  core_responding;

    fpga_wasm_board u_core (
        .clk        (mclk),
        .rst_n      (~rst),
        .en         (1'b1),
        .rxd        ({7'b0, rsync[2]}),
        .ready      (core_ready),
        .txd        (core_txd),
        .tsr        (), .tcnt (), .tbi (), .txidx (), .ms (), .pc (), .sp (),
        .err        (), .res (), .scrap_op (), .scrap_imm (), .frame_go (),
        .fstate     (), .bidx (), .opq (), .seqq (), .rxnew (), .rxbyte (),
        .rxst       (), .rxcnt (), .rbi (), .rxsr (), .rs0 (), .rs1 (), .rs2 (),
        .ticks      (), .frames (), .drops (), .responding (core_responding)
    );

    assign uart_tx = core_txd[0];
    assign led[0]  = beat[25];
    assign led[1]  = core_ready;
    assign led[2]  = core_responding[0];
    assign led[3]  = ~rst;

endmodule
`default_nettype wire
