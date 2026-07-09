# Q1513: Recovery path stale get_last_n_blocks_hashes recovery window

## Question
Can an unprivileged attacker exploit the public state-transition surface while the next competing fork still depends on the current `mainchain_initial_blockhash` as its last common ancestor, where the attacker can use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_last_n_blocks_hashes + relayer/src/main.rs::get_last_correct_block_height
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely
- Invariant to test: relayer recovery must be able to locate the last common ancestor for realistic forks without being destabilized by third-party pruning timing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
