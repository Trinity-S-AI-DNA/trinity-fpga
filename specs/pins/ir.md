# Trinity Pins DSL — IR Model

## Minimal IR Specification
## Phase 1: Lexer & Parser

### Token Types
```tri
enum TokenType {
    // FPGA/board/design tokens
    keyword_design,
    keyword_on,
    keyword_board,
    keyword_fpga,
    keyword_fabric,
    keyword_bind,
    keyword_to,
    keyword_in,
    keyword_lcurly,
    keyword_lsquare,
    keyword_rsquare,

    // Value tokens
    ident,
    string,
    colon,
    semicolon,
    comma,
    dot,
    lcurly,
    lsquare,
    rsquare,
}
```

### AST Nodes
```tri
enum NodeType {
    keyword,
    ident,
    design,
    board_ref,
    signal_ref,
    binding,
    property_list,
    property,
}
```

## Status: 🚧 UNDER CONSTRUCTION

This is a minimal IR specification for the Pins DSL.
Implement as `.tri` specs when ready for full VIBEE pipeline.
