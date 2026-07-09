# Q1566: Recovery path pruned canonical hash mistaken for missing submission

## Question
Can an unprivileged attacker exploit the public state-transition surface after relayer recovery has determined a restart height from `get_last_block_header` and `get_last_n_blocks_hashes`, where the attacker can time public pruning so a previously canonical hash disappears from the mainchain map and relayer skip logic reasons about submission state from incomplete history, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_height_by_block_hash + relayer/src/main.rs::check_submission_skipped
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: time public pruning so a previously canonical hash disappears from the mainchain map and relayer skip logic reasons about submission state from incomplete history
- Invariant to test: pruned history must not make relayer submission-state checks misclassify what is canonical or missing
- Expected Immunefi impact: Contract execution flows
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
