# Q956: ZEC testnet averaging-window floor effect

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash testnet fork after the honest chain just crossed the testnet min-difficulty activation height, where the attacker can pick targets and time windows where flooring before or after division could move the computed compact target by one step in the attacker's favor, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::zcash_calculate_next_work_required + btc-types/src/u256.rs::div_rem
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash-testnet fork with chosen header bytes, Equihash solution, testnet min-difficulty timing, and branch order
- Exploit idea: pick targets and time windows where flooring before or after division could move the computed compact target by one step in the attacker's favor
- Invariant to test: the floor behavior in Zcash retarget math must match the intended rational calculation for every boundary case
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz target and timespan boundaries against a reference implementation and compare the resulting `expected_bits`.
