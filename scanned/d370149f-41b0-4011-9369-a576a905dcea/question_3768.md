# Q3768: Recovery stale get_last_n_blocks_hashes recovery window false canonical trust

## Question
Can an unprivileged attacker exploit the public state-transition surface while `headers_pool` still contains displaced fork headers that no longer have a mainchain height mapping, where the attacker can use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely, so that relayer recovery or downstream settlement trusts a branch that should no longer be canonical?

## Target
- File/function: contract/src/lib.rs::get_last_n_blocks_hashes + relayer/src/main.rs::get_last_correct_block_height
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely to trigger false canonical trust
- Invariant to test: relayer recovery must be able to locate the last common ancestor for realistic forks without being destabilized by third-party pruning timing
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
