# Q15865: method permission mismatch at execution in near_action::DeleteKeyAction

## Question
Can an unprivileged attacker submit a limited-key function-call transaction that reaches `runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs::DeleteKeyAction` with control over method name encoding, receiver, and nested callback behavior and make nearcore authorize one contract method at the key layer but execute a broader or different method path at runtime, breaking the invariant that runtime dispatch must not exceed the exact method scope authorized by the access key, and leading to unauthorized transaction?

## Target
- File/function: `runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs::DeleteKeyAction`
- Entrypoint: submit a limited-key function-call transaction
- Attacker controls: method name encoding, receiver, and nested callback behavior
- Exploit idea: authorize one contract method at the key layer but execute a broader or different method path at runtime
- Invariant to test: runtime dispatch must not exceed the exact method scope authorized by the access key
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a limited-key contract-call test that varies method encoding and callback structure and assert out-of-scope methods never execute
