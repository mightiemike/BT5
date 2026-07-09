# Q65: BTC mainnet upper retarget clamp boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin mainnet fork with the candidate fork becoming heavier by exactly one claimed work increment, where the attacker can expand the observed timespan just above the upper clamp and see whether arithmetic edge cases permit an easier target than consensus allows, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::calculate_next_work_required + btc-types/src/u256.rs::overflowing_mul
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin header fork with crafted `prev_block_hash`, `bits`, `time`, `version`, and fork order that the default relayer can observe and forward
- Exploit idea: expand the observed timespan just above the upper clamp and see whether arithmetic edge cases permit an easier target than consensus allows
- Invariant to test: difficulty clamping must remain exact at the upper bound
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamps around the upper clamp and assert the computed target never exceeds the consensus limit.
