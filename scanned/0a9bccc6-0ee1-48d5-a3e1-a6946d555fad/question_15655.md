# Q15655: partial rejection persistence in trie_recording::record_key_removal

## Question
Can an unprivileged attacker submit a transaction whose later validation step rejects after earlier state preparation work that reaches `core/store/src/trie/trie_recording.rs::record_key_removal` with control over inputs that force one storage preparation step to succeed before the path aborts and make nearcore persist one prepared state mutation even though the full transition was rejected, breaking the invariant that rejected transitions must not leave any persisted partial state behind, and leading to balance manipulation?

## Target
- File/function: `core/store/src/trie/trie_recording.rs::record_key_removal`
- Entrypoint: submit a transaction whose later validation step rejects after earlier state preparation work
- Attacker controls: inputs that force one storage preparation step to succeed before the path aborts
- Exploit idea: persist one prepared state mutation even though the full transition was rejected
- Invariant to test: rejected transitions must not leave any persisted partial state behind
- Expected Immunefi impact: Balance manipulation
- Fast validation: write a prepare-then-reject test and assert every persisted column stays unchanged after rejection
