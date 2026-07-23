# Q9262: numeric normalization fee bypass in transactions::old_timeout_format_parses_on_new_client

## Question
Can an unprivileged attacker submit transactions whose gas or deposit values stress RPC numeric parsing boundaries that reaches `chain/jsonrpc-primitives/src/types/transactions.rs::old_timeout_format_parses_on_new_client` with control over valid numeric encodings near min, max, or representation edge cases and make nearcore charge or validate one numeric value while internal execution uses another, breaking the invariant that RPC numeric normalization must preserve the exact gas and deposit values that execution will use, and leading to fee payment bypass?

## Target
- File/function: `chain/jsonrpc-primitives/src/types/transactions.rs::old_timeout_format_parses_on_new_client`
- Entrypoint: submit transactions whose gas or deposit values stress RPC numeric parsing boundaries
- Attacker controls: valid numeric encodings near min, max, or representation edge cases
- Exploit idea: charge or validate one numeric value while internal execution uses another
- Invariant to test: RPC numeric normalization must preserve the exact gas and deposit values that execution will use
- Expected Immunefi impact: Fee payment bypass
- Fast validation: write an RPC numeric edge-case test and assert the post-parse values exactly match the signed and charged values
