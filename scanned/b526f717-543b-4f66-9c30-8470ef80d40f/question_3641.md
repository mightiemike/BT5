# Q3641: Recovery public pruning removes reorg anchor proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs to trigger proof freeze
- Invariant to test: public GC must not remove the only anchor needed to recover or validate the next canonical branch
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
