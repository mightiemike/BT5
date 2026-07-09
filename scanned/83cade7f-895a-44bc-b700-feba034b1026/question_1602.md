# Q1602: Recovery path fork headers outlive canonical anchor

## Question
Can an unprivileged attacker exploit the public state-transition surface after a public caller executes repeated one-block GC calls over multiple relayer cycles, where the attacker can leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::store_fork_header + contract/src/lib.rs::remove_block_header
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references
- Invariant to test: fork headers must never become actionable once the ancestry required to validate them has been pruned away
- Expected Immunefi impact: Contract execution flows
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
