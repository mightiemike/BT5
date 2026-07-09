# Q475: LTC testnet reorg after ancestor GC

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin testnet fork with the candidate fork becoming heavier by exactly one claimed work increment, where the attacker can advance `mainchain_initial_blockhash` with public GC calls until the next heavier fork needs a pruned ancestor during validation, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/litecoin.rs::get_next_work_required + contract/src/lib.rs::reorg_chain
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin-testnet fork with scrypt-valid headers, min-difficulty gaps, and retarget-boundary timing
- Exploit idea: advance `mainchain_initial_blockhash` with public GC calls until the next heavier fork needs a pruned ancestor during validation
- Invariant to test: pruning old canonical headers must not make a valid heavier fork impossible to validate or cause validation against the wrong ancestor
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Prune to the fork point, then submit a heavier fork spanning a retarget boundary and assert canonical recovery remains correct.
