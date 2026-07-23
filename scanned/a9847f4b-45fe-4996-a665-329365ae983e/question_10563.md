# Q10563: allowance depletion bypass in adapter::view_gas_key_nonces

## Question
Can an unprivileged attacker submit repeated signed transactions from the same key that reaches `runtime/runtime/src/adapter.rs::view_gas_key_nonces` with control over nonce ordering, allowance exhaustion timing, and execution failure shape and make nearcore deplete one tracked allowance path while another path still authorizes execution, breaking the invariant that key allowance and nonce state must advance atomically with each accepted attempt, and leading to unauthorized transaction?

## Target
- File/function: `runtime/runtime/src/adapter.rs::view_gas_key_nonces`
- Entrypoint: submit repeated signed transactions from the same key
- Attacker controls: nonce ordering, allowance exhaustion timing, and execution failure shape
- Exploit idea: deplete one tracked allowance path while another path still authorizes execution
- Invariant to test: key allowance and nonce state must advance atomically with each accepted attempt
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a repeated-submission test around allowance exhaustion and assert no extra transaction executes after allowance is spent
