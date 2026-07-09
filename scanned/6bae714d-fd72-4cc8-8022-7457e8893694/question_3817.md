# Q3817: Recovery fork-point at retention boundary duplicate economic settlement

## Question
Can an unprivileged attacker exploit the public state-transition surface immediately after `run_mainchain_gc` removes the first canonical height in storage, where the attacker can make the next realistic fork point coincide with the oldest retained canonical block and then force public pruning before the heavier branch is fully submitted, so that the same cross-chain event can be processed twice around a short reorg or stale getter window?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::submit_block_header_inner + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: make the next realistic fork point coincide with the oldest retained canonical block and then force public pruning before the heavier branch is fully submitted to trigger duplicate economic settlement
- Invariant to test: a realistic fork must not become un-recoverable merely because the fork point was exactly at the retention boundary
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
