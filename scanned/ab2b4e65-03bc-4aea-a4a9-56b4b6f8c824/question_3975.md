# Q3975: Recovery fork headers outlive canonical anchor false canonical trust

## Question
Can an unprivileged attacker exploit the public state-transition surface after a public caller executes repeated one-block GC calls over multiple relayer cycles, where the attacker can leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references, so that relayer recovery or downstream settlement trusts a branch that should no longer be canonical?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::store_fork_header + contract/src/lib.rs::remove_block_header
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: leave fork-only headers in storage after their canonical ancestor has been pruned and see whether later state transitions rely on those dangling references to trigger false canonical trust
- Invariant to test: fork headers must never become actionable once the ancestry required to validate them has been pruned away
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
