# Q691: DOGE testnet branch-size power-of-two mismatch

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin testnet AuxPoW fork with the parent header and child header delivered in different relayer batches, where the attacker can choose a branch layout whose effective size only matches `1 << len(chain_merkle_proof)` under the contract's interpretation, not the actual AuxPoW witness semantics, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::check_aux
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin-testnet fork or AuxPoW package with chosen `version`, parent header, timestamp gaps, and merged-mining witness data
- Exploit idea: choose a branch layout whose effective size only matches `1 << len(chain_merkle_proof)` under the contract's interpretation, not the actual AuxPoW witness semantics
- Invariant to test: branch-size validation must bind to the real AuxPoW tree shape and not admit an attacker-sized witness
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Generate AuxPoW witnesses around branch-size boundaries and compare validation with a reference implementation.
