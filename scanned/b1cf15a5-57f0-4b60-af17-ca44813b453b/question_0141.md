# Q141: BTC testnet median-time-past fork window mix

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork after first storing the attacker fork as non-canonical and then extending it until it barely outruns the honest tip, where the attacker can make the candidate header sit near the fork point so the 11-header median-time-past walk could accidentally mix canonical and fork ancestry and lower the timestamp floor, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::check_pow + contract/src/utils.rs::get_median_time_past
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: make the candidate header sit near the fork point so the 11-header median-time-past walk could accidentally mix canonical and fork ancestry and lower the timestamp floor
- Invariant to test: median-time-past must be derived only from the candidate chain's actual parents
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Create a forked 11-header window around the split point and assert that only the fork ancestry influences the timestamp check.
