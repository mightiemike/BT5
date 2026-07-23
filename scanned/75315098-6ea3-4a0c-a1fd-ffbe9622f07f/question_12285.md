# Q12285: receipt ordering nondeterminism in ethabi_utils::AbiTuple

## Question
Can an unprivileged attacker submit transactions whose callbacks and refunds interleave across shards that reaches `runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs::AbiTuple` with control over contract logic that emits multiple receipts with attacker-chosen ordering pressure and make nearcore let runtime-visible ordering depend on a noncanonical iteration or merge order, breaking the invariant that the same accepted transaction and receipt set must produce one deterministic execution order and state root, and leading to consensus flaws?

## Target
- File/function: `runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs::AbiTuple`
- Entrypoint: submit transactions whose callbacks and refunds interleave across shards
- Attacker controls: contract logic that emits multiple receipts with attacker-chosen ordering pressure
- Exploit idea: let runtime-visible ordering depend on a noncanonical iteration or merge order
- Invariant to test: the same accepted transaction and receipt set must produce one deterministic execution order and state root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a deterministic multi-node or property test that replays the same receipt set under different internal ordering and assert identical final roots
