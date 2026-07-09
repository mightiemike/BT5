# Q1498: Recovery path tip oracle changes during recovery

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can combine public pruning and a short reorg so the relayer recovers from a tip header that no longer has a coherent backward window, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_last_block_header + relayer/src/main.rs::get_last_correct_block_height
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: combine public pruning and a short reorg so the relayer recovers from a tip header that no longer has a coherent backward window
- Invariant to test: the tip header and backward recovery window must remain coherent throughout relayer recovery
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
