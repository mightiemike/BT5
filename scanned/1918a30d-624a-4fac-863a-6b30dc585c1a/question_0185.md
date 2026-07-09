# Q185: BTC testnet lower retarget clamp boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork with the candidate fork becoming heavier by exactly one claimed work increment, where the attacker can compress the observed timespan just below the lower clamp and see whether arithmetic edge cases still derive an attacker-favorable easier target, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::calculate_next_work_required + btc-types/src/u256.rs::overflowing_mul
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: compress the observed timespan just below the lower clamp and see whether arithmetic edge cases still derive an attacker-favorable easier target
- Invariant to test: difficulty clamping must remain exact at the lower bound
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamps around the lower clamp and compare the resulting compact target with the reference implementation.
