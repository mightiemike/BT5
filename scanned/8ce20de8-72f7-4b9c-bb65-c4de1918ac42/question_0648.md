# Q648: DOGE testnet auxpow chain root placement ambiguity

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin testnet AuxPoW fork while downstream bridge logic is waiting for the first proof on the attacker branch, where the attacker can shape the parent coinbase script so the hex-encoded chain root appears in an unintended position while still satisfying the current string-search based commitment checks, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::check_aux + btc-types/src/aux.rs::get_coinbase_tx
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin-testnet fork or AuxPoW package with chosen `version`, parent header, timestamp gaps, and merged-mining witness data
- Exploit idea: shape the parent coinbase script so the hex-encoded chain root appears in an unintended position while still satisfying the current string-search based commitment checks
- Invariant to test: AuxPoW commitment parsing must bind to the actual merged-mining commitment location, not any matching hex substring in the parent coinbase
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Build parent coinbase scripts with repeated commitment-like substrings and assert only the canonical commitment layout is accepted.
