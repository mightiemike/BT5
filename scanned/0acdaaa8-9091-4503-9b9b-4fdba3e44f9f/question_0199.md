# Q199: BTC testnet upper retarget clamp boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork while the relayer is comparing its local tip with `get_last_block_header` during recovery, where the attacker can expand the observed timespan just above the upper clamp and see whether arithmetic edge cases permit an easier target than consensus allows, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::calculate_next_work_required + btc-types/src/u256.rs::overflowing_mul
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: expand the observed timespan just above the upper clamp and see whether arithmetic edge cases permit an easier target than consensus allows
- Invariant to test: difficulty clamping must remain exact at the upper bound
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamps around the upper clamp and assert the computed target never exceeds the consensus limit.
