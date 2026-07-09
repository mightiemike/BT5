# Q3950: Recovery height-map rewrite versus cached hash proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface while canonical history is just barely larger than `gc_threshold`, where the attacker can race a height lookup against a reorg so downstream systems act on a stale canonical hash after that height is rewritten, so that a valid pending bridge proof becomes permanently unprovable because its historical anchor was pruned at the wrong moment?

## Target
- File/function: contract/src/lib.rs::get_block_hash_by_height + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: race a height lookup against a reorg so downstream systems act on a stale canonical hash after that height is rewritten to trigger proof freeze
- Invariant to test: height-to-hash answers used for proofs and settlement must not remain trustworthy after the height has been reassigned
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
