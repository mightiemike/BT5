# Q14371: duplicate acceptance across retries in signer_overlay::AccountEntry

## Question
Can an unprivileged attacker resubmit the same signed transaction through normal user-accessible paths that reaches `chain/chain/src/runtime/signer_overlay.rs::AccountEntry` with control over broadcast order, retry timing, and equivalent encodings of the same logical transaction and make nearcore accept the same logical authorization twice under different admission paths, breaking the invariant that one logical signed transaction may produce at most one accepted execution path, and leading to transaction manipulation?

## Target
- File/function: `chain/chain/src/runtime/signer_overlay.rs::AccountEntry`
- Entrypoint: resubmit the same signed transaction through normal user-accessible paths
- Attacker controls: broadcast order, retry timing, and equivalent encodings of the same logical transaction
- Exploit idea: accept the same logical authorization twice under different admission paths
- Invariant to test: one logical signed transaction may produce at most one accepted execution path
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a retry test that sends equivalent signed payloads through multiple admission paths and assert only one is accepted
