# Q737: DOGE testnet digishield protocol-switch boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin testnet AuxPoW fork after the attacker stores the fork as non-canonical and only later adds the AuxPoW witness that makes it heavier, where the attacker can straddle the height-145000 protocol switch with a crafted fork so the contract chooses the wrong adjustment interval or wrong ancestor depth, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::get_next_work_required
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin-testnet fork or AuxPoW package with chosen `version`, parent header, timestamp gaps, and merged-mining witness data
- Exploit idea: straddle the height-145000 protocol switch with a crafted fork so the contract chooses the wrong adjustment interval or wrong ancestor depth
- Invariant to test: the switch between legacy retargeting and Digishield must happen at the exact Dogecoin height on every branch
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Construct branches around height 145000 and compare the contract's expected `bits` with the reference implementation.
