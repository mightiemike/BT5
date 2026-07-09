# Q285: LTC mainnet local time truncation edge

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin mainnet fork with the candidate fork becoming heavier by exactly one claimed work increment, where the attacker can land a candidate timestamp exactly on the millisecond-to-second truncation boundary used by `env::block_timestamp_ms() / 1000`, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/litecoin.rs::check_pow + contract/src/lib.rs::submit_block_header
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin fork with scrypt-valid headers, crafted `bits`, `time`, and fork order around difficulty boundaries
- Exploit idea: land a candidate timestamp exactly on the millisecond-to-second truncation boundary used by `env::block_timestamp_ms() / 1000`
- Invariant to test: future-time enforcement must not accept a header because of truncation artifacts at the local-time boundary
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mock NEAR block time around the boundary and assert that borderline future headers are rejected before any canonical-state mutation.
