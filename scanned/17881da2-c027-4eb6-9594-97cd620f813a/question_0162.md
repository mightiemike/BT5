# Q162: BTC testnet compact target normalization drift

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork while a public caller repeatedly advances `mainchain_initial_blockhash` with `run_mainchain_gc` between relayer submissions, where the attacker can use compact targets near the sign-bit or leading-zero boundary so local normalization diverges from the source chain's canonical encoding, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: btc-types/src/utils.rs::target_from_bits + btc-types/src/u256.rs::target_to_bits + contract/src/bitcoin.rs::check_pow
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: use compact targets near the sign-bit or leading-zero boundary so local normalization diverges from the source chain's canonical encoding
- Invariant to test: compact target parsing and re-encoding must not turn a non-canonical or easier target into an accepted header
- Expected Immunefi impact: Cryptographic flaw
- Fast validation: Fuzz compact `bits` encodings around normalization edges and compare acceptance against the reference chain implementation.
