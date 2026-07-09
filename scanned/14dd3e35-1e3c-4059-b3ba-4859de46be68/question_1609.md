# Q1609: Recovery path fork-point at retention boundary

## Question
Can an unprivileged attacker exploit the public state-transition surface after a public caller executes repeated one-block GC calls over multiple relayer cycles, where the attacker can make the next realistic fork point coincide with the oldest retained canonical block and then force public pruning before the heavier branch is fully submitted, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::submit_block_header_inner + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: make the next realistic fork point coincide with the oldest retained canonical block and then force public pruning before the heavier branch is fully submitted
- Invariant to test: a realistic fork must not become un-recoverable merely because the fork point was exactly at the retention boundary
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
