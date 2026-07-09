# Q3818: Recovery getter split-brain around displaced tip proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface immediately after `run_mainchain_gc` removes the first canonical height in storage, where the attacker can observe whether getter calls can expose a displaced tip and a new canonical height mapping in separate transactions such that downstream settlement acts on a split-brain oracle, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::get_last_block_header + contract/src/lib.rs::get_last_block_height + contract/src/lib.rs::get_height_by_block_hash
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: observe whether getter calls can expose a displaced tip and a new canonical height mapping in separate transactions such that downstream settlement acts on a split-brain oracle to trigger proof freeze
- Invariant to test: public getters must expose a coherent canonical view after reorg and GC transitions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
