# Q16233: state initialization bleed in logic::create_partial_chunk

## Question
Can an unprivileged attacker deploy or initialize contracts across multiple attacker-controlled accounts that reaches `chain/chunks/src/logic.rs::create_partial_chunk` with control over account ids, initialization order, and global contract references and make nearcore reuse or cross-wire initialization state between logically separate accounts or contract instances, breaking the invariant that contract initialization and global state binding must stay account-local unless explicitly specified, and leading to contracts execution flows?

## Target
- File/function: `chain/chunks/src/logic.rs::create_partial_chunk`
- Entrypoint: deploy or initialize contracts across multiple attacker-controlled accounts
- Attacker controls: account ids, initialization order, and global contract references
- Exploit idea: reuse or cross-wire initialization state between logically separate accounts or contract instances
- Invariant to test: contract initialization and global state binding must stay account-local unless explicitly specified
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a two-account deploy-and-init test and assert one account cannot inherit another account’s initialization state
