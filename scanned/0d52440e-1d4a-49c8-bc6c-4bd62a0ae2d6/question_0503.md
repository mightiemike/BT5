# Q503: LTC testnet mainchain-height lookback on competing fork

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin testnet fork with the critical boundary headers split across two relayer-signed transactions in the same sync cycle, where the attacker can force retarget validation on a fork whose historical block at a needed height differs from the current canonical block occupying that height, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/lib.rs::get_header_by_height + contract/src/litecoin.rs::get_next_work_required + contract/src/lib.rs::store_fork_header
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin-testnet fork with scrypt-valid headers, min-difficulty gaps, and retarget-boundary timing
- Exploit idea: force retarget validation on a fork whose historical block at a needed height differs from the current canonical block occupying that height
- Invariant to test: historical lookups used during fork validation must follow the fork branch, not the current mainchain index
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Create two branches with different timestamps at the same historical height and confirm the verifier uses the correct branch for retargeting.
