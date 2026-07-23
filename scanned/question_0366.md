# Q366: rpc transaction parsing ambiguity in call_function::rpc_from

## Question
Can an unprivileged attacker submit a signed transaction through a default-enabled JSON-RPC method that reaches `chain/jsonrpc/src/api/call_function.rs::rpc_from` with control over serialized transaction fields whose alternate encodings remain syntactically valid and make nearcore parse one signed payload into different effective transaction fields across admission paths, breaking the invariant that every RPC transaction route must normalize a signed payload into one canonical transaction image, and leading to unauthorized transaction?

## Target
- File/function: `chain/jsonrpc/src/api/call_function.rs::rpc_from`
- Entrypoint: submit a signed transaction through a default-enabled JSON-RPC method
- Attacker controls: serialized transaction fields whose alternate encodings remain syntactically valid
- Exploit idea: parse one signed payload into different effective transaction fields across admission paths
- Invariant to test: every RPC transaction route must normalize a signed payload into one canonical transaction image
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write an RPC parsing test that sends equivalent serialized variants and assert they normalize identically or are rejected
