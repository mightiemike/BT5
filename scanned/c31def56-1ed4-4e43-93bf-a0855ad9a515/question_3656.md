# Q3656: Recovery pruned canonical hash mistaken for missing submission proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can time public pruning so a previously canonical hash disappears from the mainchain map and relayer skip logic reasons about submission state from incomplete history, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::get_height_by_block_hash + relayer/src/main.rs::check_submission_skipped
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: time public pruning so a previously canonical hash disappears from the mainchain map and relayer skip logic reasons about submission state from incomplete history to trigger proof freeze
- Invariant to test: pruned history must not make relayer submission-state checks misclassify what is canonical or missing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
