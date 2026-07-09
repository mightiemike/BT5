# Q3809: Recovery oldest-retained proof freeze proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface immediately after `run_mainchain_gc` removes the first canonical height in storage, where the attacker can prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::verify_transaction_inclusion_v2
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it to trigger proof freeze
- Invariant to test: public GC must not let a third party permanently freeze a valid pending bridge proof by deleting the earliest still-relevant anchor
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
