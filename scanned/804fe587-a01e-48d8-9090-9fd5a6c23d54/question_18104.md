# Q18104: seat-assignment balance split in adapter::epoch_exists

## Question
Can an unprivileged attacker submit stake changes that coincide with shard or seat reassignment logic that reaches `chain/epoch-manager/src/adapter.rs::epoch_exists` with control over stake amounts and account placement near assignment edge cases and make nearcore commit stake balance under one assignment view but consume it under another, breaking the invariant that seat or shard assignment must use the same canonical stake balances that execution commits, and leading to consensus flaws?

## Target
- File/function: `chain/epoch-manager/src/adapter.rs::epoch_exists`
- Entrypoint: submit stake changes that coincide with shard or seat reassignment logic
- Attacker controls: stake amounts and account placement near assignment edge cases
- Exploit idea: commit stake balance under one assignment view but consume it under another
- Invariant to test: seat or shard assignment must use the same canonical stake balances that execution commits
- Expected Immunefi impact: Consensus flaws
- Fast validation: write an assignment-edge test and assert stake balances and resulting assignments stay aligned
