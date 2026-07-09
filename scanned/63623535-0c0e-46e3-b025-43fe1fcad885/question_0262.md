# Q262: LTC mainnet fork retarget ancestor confusion

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin mainnet fork while a public caller repeatedly advances `mainchain_initial_blockhash` with `run_mainchain_gc` between relayer submissions, where the attacker can place the fork split just before a retarget boundary so `get_header_by_height` reads the current mainchain block at `first_block_height` instead of the ancestor on the candidate fork, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/litecoin.rs::get_next_work_required + contract/src/lib.rs::get_header_by_height + contract/src/lib.rs::submit_block_header_inner
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin fork with scrypt-valid headers, crafted `bits`, `time`, and fork order around difficulty boundaries
- Exploit idea: place the fork split just before a retarget boundary so `get_header_by_height` reads the current mainchain block at `first_block_height` instead of the ancestor on the candidate fork
- Invariant to test: retargeting a fork candidate must use the fork lineage itself, not whichever block currently occupies that height in the mainchain map
- Expected Immunefi impact: Light client verification bypass leading to stealing or loss of funds
- Fast validation: Construct a fork that diverges before a retarget boundary, submit it through the relayer path, and compare the computed `bits` against a reference node using the fork's true ancestor history.
