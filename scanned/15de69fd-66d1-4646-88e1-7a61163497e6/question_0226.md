# Q226: BTC testnet version floor on non-mainchain path

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin testnet fork after relayer recovery from `PrevBlockNotFound` resumes at the last common ancestor, where the attacker can delay a stale-version header until it only becomes heavier after a fork reorg, then check whether the non-mainchain path enforces the same version floor, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::check_pow + contract/src/lib.rs::reorg_chain
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin-testnet fork with timestamp gaps, compact targets, and batch timing chosen to stress min-difficulty and retarget logic
- Exploit idea: delay a stale-version header until it only becomes heavier after a fork reorg, then check whether the non-mainchain path enforces the same version floor
- Invariant to test: headers below the version floor must be rejected identically on mainchain and fork paths
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Submit a stale-version fork until it becomes heavier and assert it never reaches canonical state.
