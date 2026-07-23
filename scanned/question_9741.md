# Q9741: finalization rollback gap in compression::encode_decode_within_limit

## Question
Can an unprivileged attacker submit transactions that partially progress through block application before later rejection that reaches `core/primitives/src/utils/compression.rs::encode_decode_within_limit` with control over user-valid transactions that trigger late validation or accounting failure and make nearcore advance finalization-visible state further than rollback logic unwinds it, breaking the invariant that block-application rejection must leave canonical state exactly as if the rejected block were never applied, and leading to stealing or loss of funds?

## Target
- File/function: `core/primitives/src/utils/compression.rs::encode_decode_within_limit`
- Entrypoint: submit transactions that partially progress through block application before later rejection
- Attacker controls: user-valid transactions that trigger late validation or accounting failure
- Exploit idea: advance finalization-visible state further than rollback logic unwinds it
- Invariant to test: block-application rejection must leave canonical state exactly as if the rejected block were never applied
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a late-failure block-application test and assert every canonical store and balance matches the pre-application snapshot
