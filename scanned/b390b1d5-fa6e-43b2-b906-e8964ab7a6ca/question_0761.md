# Q761: ZEC mainnet averaging-window ancestor mismatch

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash mainnet fork after first storing the attacker branch as non-canonical and then extending it until it barely wins fork choice, where the attacker can force the averaging window to cross the fork point so the total target or median-time history can be built from the wrong lineage, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::zcash_get_next_work_required + contract/src/utils.rs::BlocksGetter::get_prev_header
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash fork with chosen header bytes, Equihash solution, median-time history, and branch order fed through the default relayer
- Exploit idea: force the averaging window to cross the fork point so the total target or median-time history can be built from the wrong lineage
- Invariant to test: Zcash averaging-window retargeting must use only the candidate branch's own historical headers
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Construct forks that diverge inside the averaging window and compare the computed `expected_bits` with a reference node.
