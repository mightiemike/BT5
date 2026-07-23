# Q12161: noncanonical hash composition in bls12381::pairing_check

## Question
Can an unprivileged attacker submit transactions that create many hashed intermediate objects that reaches `runtime/near-vm-runner/src/logic/bls12381.rs::pairing_check` with control over ordering and grouping of actions or receipts that produce the same logical set and make nearcore compose hashes in a way that depends on a noncanonical traversal order, breaking the invariant that hash composition must be traversal-independent wherever the logical object is order-invariant, and leading to consensus flaws?

## Target
- File/function: `runtime/near-vm-runner/src/logic/bls12381.rs::pairing_check`
- Entrypoint: submit transactions that create many hashed intermediate objects
- Attacker controls: ordering and grouping of actions or receipts that produce the same logical set
- Exploit idea: compose hashes in a way that depends on a noncanonical traversal order
- Invariant to test: hash composition must be traversal-independent wherever the logical object is order-invariant
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a property test that permutes logically equivalent inputs and assert composed hashes remain identical
