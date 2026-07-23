# Q5629: unstake lock-rule bypass in rpc_handler::get_next_epoch_id_if_at_boundary

## Question
Can an unprivileged attacker submit unstake and withdraw style transactions in rapid sequence that reaches `chain/client/src/rpc_handler.rs::get_next_epoch_id_if_at_boundary` with control over timing and amounts that stress lock-period and unlock-accounting edges and make nearcore treat stake as unlocked for withdrawal on one path while another path still considers it locked, breaking the invariant that withdrawal availability must follow one canonical unlock schedule, and leading to stealing or loss of funds?

## Target
- File/function: `chain/client/src/rpc_handler.rs::get_next_epoch_id_if_at_boundary`
- Entrypoint: submit unstake and withdraw style transactions in rapid sequence
- Attacker controls: timing and amounts that stress lock-period and unlock-accounting edges
- Exploit idea: treat stake as unlocked for withdrawal on one path while another path still considers it locked
- Invariant to test: withdrawal availability must follow one canonical unlock schedule
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write an unstake-then-withdraw timing test and assert withdrawal fails until the canonical unlock point
