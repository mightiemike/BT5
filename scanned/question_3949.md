# Q3949: access-key permission confusion in signature::empty

## Question
Can an unprivileged attacker submit a signed transaction from a limited access key that reaches `core/crypto/src/signature.rs::empty` with control over method names, receiver ids, deposits, and nested action composition and make nearcore validate the access-key permission set against one action view and execute a broader action set downstream, breaking the invariant that limited access keys may authorize only their exact receiver, method, and deposit bounds, and leading to unauthorized transaction?

## Target
- File/function: `core/crypto/src/signature.rs::empty`
- Entrypoint: submit a signed transaction from a limited access key
- Attacker controls: method names, receiver ids, deposits, and nested action composition
- Exploit idea: validate the access-key permission set against one action view and execute a broader action set downstream
- Invariant to test: limited access keys may authorize only their exact receiver, method, and deposit bounds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a key-permission test that varies receiver and nested action shape and assert forbidden actions fail before state mutation
