# Q9309: allowance depletion bypass in signer::sign

## Question
Can an unprivileged attacker submit repeated signed transactions from the same key that reaches `core/crypto/src/signer.rs::sign` with control over nonce ordering, allowance exhaustion timing, and execution failure shape and make nearcore deplete one tracked allowance path while another path still authorizes execution, breaking the invariant that key allowance and nonce state must advance atomically with each accepted attempt, and leading to unauthorized transaction?

## Target
- File/function: `core/crypto/src/signer.rs::sign`
- Entrypoint: submit repeated signed transactions from the same key
- Attacker controls: nonce ordering, allowance exhaustion timing, and execution failure shape
- Exploit idea: deplete one tracked allowance path while another path still authorizes execution
- Invariant to test: key allowance and nonce state must advance atomically with each accepted attempt
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a repeated-submission test around allowance exhaustion and assert no extra transaction executes after allowance is spent
