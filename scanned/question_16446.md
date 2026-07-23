# Q16446: stale crypto-cache reuse in util::from_near_implicit_account

## Question
Can an unprivileged attacker submit repeated signed or hashed payloads that differ only in one context field that reaches `core/crypto/src/util.rs::from_near_implicit_account` with control over payload sequences designed to stress cache reuse without violating protocol limits and make nearcore reuse a cached verification or hash result after its context has materially changed, breaking the invariant that cached cryptographic results must be invalidated whenever any meaning-bearing input changes, and leading to unauthorized transaction?

## Target
- File/function: `core/crypto/src/util.rs::from_near_implicit_account`
- Entrypoint: submit repeated signed or hashed payloads that differ only in one context field
- Attacker controls: payload sequences designed to stress cache reuse without violating protocol limits
- Exploit idea: reuse a cached verification or hash result after its context has materially changed
- Invariant to test: cached cryptographic results must be invalidated whenever any meaning-bearing input changes
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a cache-reuse test with one-field mutations and assert cached results are never reused incorrectly
