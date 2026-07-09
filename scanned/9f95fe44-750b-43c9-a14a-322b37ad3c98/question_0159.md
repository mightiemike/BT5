# Q159: BTC testnet local time truncation edge

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork while the relayer is comparing its local tip with `get_last_block_header` during recovery, where the attacker can land a candidate timestamp exactly on the millisecond-to-second truncation boundary used by `env::block_timestamp_ms() / 1000`, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::check_pow + contract/src/lib.rs::submit_block_header
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: land a candidate timestamp exactly on the millisecond-to-second truncation boundary used by `env::block_timestamp_ms() / 1000`
- Invariant to test: future-time enforcement must not accept a header because of truncation artifacts at the local-time boundary
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mock NEAR block time around the boundary and assert that borderline future headers are rejected before any canonical-state mutation.
