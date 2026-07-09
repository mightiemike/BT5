# Q57: BTC mainnet lower retarget clamp boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin mainnet fork while the common ancestor still exists in `headers_pool` but no longer in the mainchain height map, where the attacker can compress the observed timespan just below the lower clamp and see whether arithmetic edge cases still derive an attacker-favorable easier target, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::calculate_next_work_required + btc-types/src/u256.rs::overflowing_mul
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin header fork with crafted `prev_block_hash`, `bits`, `time`, `version`, and fork order that the default relayer can observe and forward
- Exploit idea: compress the observed timespan just below the lower clamp and see whether arithmetic edge cases still derive an attacker-favorable easier target
- Invariant to test: difficulty clamping must remain exact at the lower bound
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamps around the lower clamp and compare the resulting compact target with the reference implementation.
