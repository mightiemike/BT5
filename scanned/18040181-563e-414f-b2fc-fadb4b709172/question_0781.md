# Q781: ZEC mainnet future-mtp limit mismatch

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash mainnet fork with the attacker branch winning by only one claimed work increment, where the attacker can land a candidate timestamp around the contract's `MAX_FUTURE_BLOCK_TIME_MTP` boundary to see whether the local constant mismatches current Zcash consensus rules, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::check_pow + btc-types/src/network.rs::MAX_FUTURE_BLOCK_TIME_MTP
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash fork with chosen header bytes, Equihash solution, median-time history, and branch order fed through the default relayer
- Exploit idea: land a candidate timestamp around the contract's `MAX_FUTURE_BLOCK_TIME_MTP` boundary to see whether the local constant mismatches current Zcash consensus rules
- Invariant to test: the future-time limit relative to median-time-past must match the intended Zcash network rules exactly
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay headers around the future-MTP boundary and compare acceptance against a reference Zcash node.
