# Q527: DOGE mainnet auxpow chain root placement ambiguity

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin mainnet AuxPoW fork after a short honest reorg replaced the block previously occupying the critical height slot, where the attacker can shape the parent coinbase script so the hex-encoded chain root appears in an unintended position while still satisfying the current string-search based commitment checks, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::check_aux + btc-types/src/aux.rs::get_coinbase_tx
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin fork or AuxPoW package with chosen `version`, coinbase script bytes, parent header, chain merkle proof, and nonce/index fields
- Exploit idea: shape the parent coinbase script so the hex-encoded chain root appears in an unintended position while still satisfying the current string-search based commitment checks
- Invariant to test: AuxPoW commitment parsing must bind to the actual merged-mining commitment location, not any matching hex substring in the parent coinbase
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Build parent coinbase scripts with repeated commitment-like substrings and assert only the canonical commitment layout is accepted.
