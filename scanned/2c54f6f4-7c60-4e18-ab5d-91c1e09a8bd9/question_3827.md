# Q3827: Recovery stale get_last_n_blocks_hashes recovery window proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface while the honest chain and attacker fork alternate as tip candidates across successive batches, where the attacker can use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::get_last_n_blocks_hashes + relayer/src/main.rs::get_last_correct_block_height
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: use public pruning and fork timing so the relayer's backward scan no longer contains the actual last common ancestor it needs to resume safely to trigger proof freeze
- Invariant to test: relayer recovery must be able to locate the last common ancestor for realistic forks without being destabilized by third-party pruning timing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
