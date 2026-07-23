# Q11802: storage delta undercharge in from_flat::get_trie_update_batch

## Question
Can an unprivileged attacker submit transactions that rapidly create, update, delete, and recreate the same keys that reaches `core/store/src/trie/from_flat.rs::get_trie_update_batch` with control over key lifecycle patterns and attached deposits that stress accounting boundaries and make nearcore calculate the charged storage delta from an intermediate snapshot rather than the committed delta, breaking the invariant that storage charging must match the net committed byte delta after the full transaction completes, and leading to fee payment bypass?

## Target
- File/function: `core/store/src/trie/from_flat.rs::get_trie_update_batch`
- Entrypoint: submit transactions that rapidly create, update, delete, and recreate the same keys
- Attacker controls: key lifecycle patterns and attached deposits that stress accounting boundaries
- Exploit idea: calculate the charged storage delta from an intermediate snapshot rather than the committed delta
- Invariant to test: storage charging must match the net committed byte delta after the full transaction completes
- Expected Immunefi impact: Fee payment bypass
- Fast validation: write a create-delete-recreate scenario and assert charged storage matches the final persisted bytes
