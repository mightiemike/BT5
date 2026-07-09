# Q988: ZEC testnet local-time plus mtp edge

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash testnet fork after the raw header bytes were parsed from the node RPC path and reserialized for contract submission, where the attacker can line up a candidate timestamp so it barely passes one of the two time windows and fails the other, then check whether ordering or truncation changes acceptance, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::check_pow
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash-testnet fork with chosen header bytes, Equihash solution, testnet min-difficulty timing, and branch order
- Exploit idea: line up a candidate timestamp so it barely passes one of the two time windows and fails the other, then check whether ordering or truncation changes acceptance
- Invariant to test: both Zcash time windows must be enforced consistently and independently at their exact boundaries
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Test timestamps at both boundaries and assert the header is accepted only when both windows are satisfied.
