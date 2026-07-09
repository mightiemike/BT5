# Q756: DOGE testnet auxpow commitment first-20-bytes boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin testnet AuxPoW fork while downstream bridge logic is waiting for the first proof on the attacker branch, where the attacker can place the chain merkle root exactly around the first-20-bytes fallback boundary to see whether the contract accepts a layout that the reference parser would reject, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::check_aux
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin-testnet fork or AuxPoW package with chosen `version`, parent header, timestamp gaps, and merged-mining witness data
- Exploit idea: place the chain merkle root exactly around the first-20-bytes fallback boundary to see whether the contract accepts a layout that the reference parser would reject
- Invariant to test: the fallback commitment rule must not accept an off-by-one root position in the parent coinbase
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Generate parent coinbase scripts around the first-20-bytes boundary and compare acceptance against a reference parser.
