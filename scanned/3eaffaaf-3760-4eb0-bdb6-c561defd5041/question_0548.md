# Q548: DOGE mainnet auxpow index derivation wraparound

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Dogecoin mainnet AuxPoW fork after the honest chain just crossed the Digishield switch height, where the attacker can pick `n_nonce`, branch height, and chain-id bytes that stress the wrapped arithmetic in `get_expected_index` and produce an index the contract accepts but the source chain would reject, so that an invalid Dogecoin header becomes canonical and downstream bridge logic treats non-Dogecoin-final state as trusted?

## Target
- File/function: contract/src/dogecoin.rs::get_expected_index + contract/src/dogecoin.rs::check_aux
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Dogecoin fork or AuxPoW package with chosen `version`, coinbase script bytes, parent header, chain merkle proof, and nonce/index fields
- Exploit idea: pick `n_nonce`, branch height, and chain-id bytes that stress the wrapped arithmetic in `get_expected_index` and produce an index the contract accepts but the source chain would reject
- Invariant to test: the AuxPoW expected-index calculation must match the Dogecoin reference rules for all branch heights and nonce values
- Expected Immunefi impact: Cryptographic flaw
- Fast validation: Fuzz `n_nonce`, `chain_id`, and branch heights against a reference implementation and assert the computed index always matches.
