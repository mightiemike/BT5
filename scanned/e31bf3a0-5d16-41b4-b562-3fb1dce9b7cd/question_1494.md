# Q1494: Recovery path height-map rewrite versus cached hash

## Question
Can an unprivileged attacker exploit the public state-transition surface while the relayer is walking backward through `get_last_n_blocks_hashes` to find the last correct height, where the attacker can race a height lookup against a reorg so downstream systems act on a stale canonical hash after that height is rewritten, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_block_hash_by_height + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: race a height lookup against a reorg so downstream systems act on a stale canonical hash after that height is rewritten
- Invariant to test: height-to-hash answers used for proofs and settlement must not remain trustworthy after the height has been reassigned
- Expected Immunefi impact: Contract execution flows
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
