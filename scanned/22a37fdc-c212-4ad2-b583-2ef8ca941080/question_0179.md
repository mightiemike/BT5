# Q179: BTC testnet chainwork ranking edge case

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork while the relayer is comparing its local tip with `get_last_block_header` during recovery, where the attacker can choose targets near the easiest valid boundary and check whether `work_from_bits` over-credits a lower-security fork so it outranks the honest tip, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: btc-types/src/utils.rs::work_from_bits + btc-types/src/u256.rs::inverse + contract/src/lib.rs::submit_block_header_inner
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: choose targets near the easiest valid boundary and check whether `work_from_bits` over-credits a lower-security fork so it outranks the honest tip
- Invariant to test: fork choice must rank cumulative work exactly as the source chain would, especially near target extremes
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build competing forks with near-limit targets and assert the promoted tip matches reference chainwork ordering.
