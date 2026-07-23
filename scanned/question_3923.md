# Q3923: field loss in RPC conversion in transactions::to_signed_tx

## Question
Can an unprivileged attacker submit a signed transaction whose fields sit on conversion edge cases that reaches `chain/jsonrpc-primitives/src/types/transactions.rs::to_signed_tx` with control over gas, deposit, action order, and signature-bearing fields near representation boundaries and make nearcore drop, rewrite, or default one security-critical field while converting RPC input into the internal transaction object, breaking the invariant that RPC conversion must preserve every security-critical transaction field exactly, and leading to unauthorized transaction?

## Target
- File/function: `chain/jsonrpc-primitives/src/types/transactions.rs::to_signed_tx`
- Entrypoint: submit a signed transaction whose fields sit on conversion edge cases
- Attacker controls: gas, deposit, action order, and signature-bearing fields near representation boundaries
- Exploit idea: drop, rewrite, or default one security-critical field while converting RPC input into the internal transaction object
- Invariant to test: RPC conversion must preserve every security-critical transaction field exactly
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a round-trip conversion test that compares every signed field before and after RPC decoding
