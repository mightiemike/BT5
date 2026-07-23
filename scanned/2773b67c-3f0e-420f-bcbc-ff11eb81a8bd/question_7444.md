# Q7444: staking authority mismatch in sticky_resharding::ChildLoad

## Question
Can an unprivileged attacker submit stake transactions that rotate keys or signer context around the same account that reaches `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::ChildLoad` with control over key changes, stake instructions, and follow-up stake or unstake actions and make nearcore let a stale staking authority continue to move staked balance after rotation, breaking the invariant that staking authority changes must fully revoke prior staking control, and leading to unauthorized transaction?

## Target
- File/function: `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::ChildLoad`
- Entrypoint: submit stake transactions that rotate keys or signer context around the same account
- Attacker controls: key changes, stake instructions, and follow-up stake or unstake actions
- Exploit idea: let a stale staking authority continue to move staked balance after rotation
- Invariant to test: staking authority changes must fully revoke prior staking control
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a key-rotation-plus-stake test and assert old authority cannot move staked balance
