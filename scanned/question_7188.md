# Q7188: proof-context omission in pending::contains_block_hash

## Question
Can an unprivileged attacker submit transactions or delegated messages that rely on hashed or signed subobjects that reaches `chain/chain/src/pending.rs::contains_block_hash` with control over subobject fields whose security context differs even when the bytes mostly match and make nearcore omit one context field from a binding hash or signature check and accept a wrong-context object, breaking the invariant that every authenticated subobject must bind all context needed to preserve its meaning, and leading to cryptographic flaws?

## Target
- File/function: `chain/chain/src/pending.rs::contains_block_hash`
- Entrypoint: submit transactions or delegated messages that rely on hashed or signed subobjects
- Attacker controls: subobject fields whose security context differs even when the bytes mostly match
- Exploit idea: omit one context field from a binding hash or signature check and accept a wrong-context object
- Invariant to test: every authenticated subobject must bind all context needed to preserve its meaning
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a context-omission test that changes one meaning-bearing field and assert the object no longer verifies
