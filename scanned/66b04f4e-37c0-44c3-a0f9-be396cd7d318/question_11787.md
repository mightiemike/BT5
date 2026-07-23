# Q11787: storage delta undercharge in opener::open

## Question
Can an unprivileged attacker submit transactions that rapidly create, update, delete, and recreate the same keys that reaches `core/store/src/node_storage/opener.rs::open` with control over key lifecycle patterns and attached deposits that stress accounting boundaries and make nearcore calculate the charged storage delta from an intermediate snapshot rather than the committed delta, breaking the invariant that storage charging must match the net committed byte delta after the full transaction completes, and leading to fee payment bypass?

## Target
- File/function: `core/store/src/node_storage/opener.rs::open`
- Entrypoint: submit transactions that rapidly create, update, delete, and recreate the same keys
- Attacker controls: key lifecycle patterns and attached deposits that stress accounting boundaries
- Exploit idea: calculate the charged storage delta from an intermediate snapshot rather than the committed delta
- Invariant to test: storage charging must match the net committed byte delta after the full transaction completes
- Expected Immunefi impact: Fee payment bypass
- Fast validation: write a create-delete-recreate scenario and assert charged storage matches the final persisted bytes
