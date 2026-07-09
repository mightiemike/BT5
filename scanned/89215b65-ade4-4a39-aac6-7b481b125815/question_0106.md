# Q106: BTC mainnet pow hash byte-order boundary

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin mainnet fork after relayer recovery from `PrevBlockNotFound` resumes at the last common ancestor, where the attacker can use near-boundary PoW hashes to see whether little-endian conversion into `U256` can flip an invalid hash into an accepted one, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: btc-types/src/btc_header.rs::block_hash_pow + btc-types/src/u256.rs::from_le_bytes + contract/src/bitcoin.rs::check_pow
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin header fork with crafted `prev_block_hash`, `bits`, `time`, `version`, and fork order that the default relayer can observe and forward
- Exploit idea: use near-boundary PoW hashes to see whether little-endian conversion into `U256` can flip an invalid hash into an accepted one
- Invariant to test: PoW comparison must use the exact byte order required by the source chain for every near-boundary header
- Expected Immunefi impact: Cryptographic flaw
- Fast validation: Generate headers whose PoW hashes are near the target boundary and compare acceptance with a reference node.
