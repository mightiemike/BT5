# Q801: ZEC mainnet equihash serialization mismatch

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash mainnet fork while the relayer is recovering from a prior `PrevBlockNotFound` condition, where the attacker can choose header fields whose serialized Equihash input is sensitive to omitted or differently ordered bytes so the contract verifies a solution over bytes the source chain would not hash, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: btc-types/src/zcash_header.rs::get_block_header_vec_for_equihash + contract/src/zcash.rs::check_pow
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash fork with chosen header bytes, Equihash solution, median-time history, and branch order fed through the default relayer
- Exploit idea: choose header fields whose serialized Equihash input is sensitive to omitted or differently ordered bytes so the contract verifies a solution over bytes the source chain would not hash
- Invariant to test: Equihash verification must hash exactly the byte sequence defined by Zcash consensus
- Expected Immunefi impact: Cryptographic flaw
- Fast validation: Compare the contract's Equihash input bytes with a reference Zcash implementation for boundary-case headers.
