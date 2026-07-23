# Q13211: account creation authority bleed in signable_message::nep_366_wrong_nep

## Question
Can an unprivileged attacker submit create-account or add-key style transactions that reaches `core/primitives/src/signable_message.rs::nep_366_wrong_nep` with control over new account ids, key sets, and ordering against related transactions and make nearcore carry authority from a previous account or key state into a newly created or rotated one, breaking the invariant that account creation and key rotation must fully replace prior authority boundaries, and leading to unauthorized transaction?

## Target
- File/function: `core/primitives/src/signable_message.rs::nep_366_wrong_nep`
- Entrypoint: submit create-account or add-key style transactions
- Attacker controls: new account ids, key sets, and ordering against related transactions
- Exploit idea: carry authority from a previous account or key state into a newly created or rotated one
- Invariant to test: account creation and key rotation must fully replace prior authority boundaries
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a create-account plus rotate-key scenario and assert old authority cannot authorize the new state
