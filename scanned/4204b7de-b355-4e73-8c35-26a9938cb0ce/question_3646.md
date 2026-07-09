# Q3646: Recovery fork headers outlive canonical anchor duplicate economic settlement

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references, so that the same cross-chain event can be processed twice around a short reorg or stale getter window?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::store_fork_header + contract/src/lib.rs::remove_block_header
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references to trigger duplicate economic settlement
- Invariant to test: fork headers must never become actionable once the ancestry required to validate them has been pruned away
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
