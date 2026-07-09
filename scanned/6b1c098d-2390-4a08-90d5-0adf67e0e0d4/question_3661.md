# Q3661: Recovery oldest-retained proof freeze duplicate economic settlement

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it, so that the same cross-chain event can be processed twice around a short reorg or stale getter window?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::verify_transaction_inclusion_v2
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it to trigger duplicate economic settlement
- Invariant to test: public GC must not let a third party permanently freeze a valid pending bridge proof by deleting the earliest still-relevant anchor
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
