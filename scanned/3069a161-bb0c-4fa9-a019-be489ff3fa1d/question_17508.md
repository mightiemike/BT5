# Q17508: state initialization bleed in context::is_view

## Question
Can an unprivileged attacker deploy or initialize contracts across multiple attacker-controlled accounts that reaches `runtime/near-vm-runner/src/logic/context.rs::is_view` with control over account ids, initialization order, and global contract references and make nearcore reuse or cross-wire initialization state between logically separate accounts or contract instances, breaking the invariant that contract initialization and global state binding must stay account-local unless explicitly specified, and leading to contracts execution flows?

## Target
- File/function: `runtime/near-vm-runner/src/logic/context.rs::is_view`
- Entrypoint: deploy or initialize contracts across multiple attacker-controlled accounts
- Attacker controls: account ids, initialization order, and global contract references
- Exploit idea: reuse or cross-wire initialization state between logically separate accounts or contract instances
- Invariant to test: contract initialization and global state binding must stay account-local unless explicitly specified
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a two-account deploy-and-init test and assert one account cannot inherit another account’s initialization state
