# Q612: DOGE mainnet testnet min-difficulty persistence

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin mainnet AuxPoW fork while downstream bridge logic is waiting for the first proof on the attacker branch, where the attacker can extend a testnet easy-mined block sequence so the contract may preserve the min-difficulty exception one header longer than consensus allows, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::allow_min_difficulty_for_block + contract/src/dogecoin.rs::get_next_work_required
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin fork or AuxPoW package with chosen `version`, coinbase script bytes, parent header, chain merkle proof, and nonce/index fields
- Exploit idea: extend a testnet easy-mined block sequence so the contract may preserve the min-difficulty exception one header longer than consensus allows
- Invariant to test: Dogecoin testnet min-difficulty exceptions must expire exactly when consensus says they do
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamp gaps around the exception boundary and assert the next `bits` value snaps back exactly when the reference client does.
