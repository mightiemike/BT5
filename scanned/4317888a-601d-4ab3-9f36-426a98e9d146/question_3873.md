# Q3873: Recovery tip oracle changes during recovery false canonical trust

## Question
Can an unprivileged attacker exploit the public state-transition surface after relayer recovery has determined a restart height from `get_last_block_header` and `get_last_n_blocks_hashes`, where the attacker can combine public pruning and a short reorg so the relayer recovers from a tip header that no longer has a coherent backward window, so that relayer recovery or downstream settlement trusts a branch that should no longer be canonical?

## Target
- File/function: contract/src/lib.rs::get_last_block_header + relayer/src/main.rs::get_last_correct_block_height
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: combine public pruning and a short reorg so the relayer recovers from a tip header that no longer has a coherent backward window to trigger false canonical trust
- Invariant to test: the tip header and backward recovery window must remain coherent throughout relayer recovery
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
