# Q910: ZEC testnet testnet min-difficulty activation edge

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Zcash testnet fork with the common ancestor sitting exactly at the oldest retained height, where the attacker can stress the exact height where testnet min-difficulty becomes allowed so the contract may apply the exception one block too early or too late, so that the contract accepts a Zcash header or branch that the source chain would reject and downstream bridge logic trusts a false canonical state?

## Target
- File/function: contract/src/zcash.rs::zcash_get_next_work_required
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Zcash-testnet fork with chosen header bytes, Equihash solution, testnet min-difficulty timing, and branch order
- Exploit idea: stress the exact height where testnet min-difficulty becomes allowed so the contract may apply the exception one block too early or too late
- Invariant to test: Zcash testnet min-difficulty must activate at the exact consensus height on every branch
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay timestamps around the activation height and compare the expected `bits` with the reference implementation.
