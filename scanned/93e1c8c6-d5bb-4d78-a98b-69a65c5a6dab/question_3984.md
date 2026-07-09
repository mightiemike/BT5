# Q3984: Recovery mainchain-size implied history drift false canonical trust

## Question
Can an unprivileged attacker exploit the public state-transition surface after a public caller executes repeated one-block GC calls over multiple relayer cycles, where the attacker can drive GC and reorg transitions until `get_mainchain_size` implies a history window that getters and proof APIs can no longer actually serve coherently, so that relayer recovery or downstream settlement trusts a branch that should no longer be canonical?

## Target
- File/function: contract/src/lib.rs::get_mainchain_size + contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: drive GC and reorg transitions until `get_mainchain_size` implies a history window that getters and proof APIs can no longer actually serve coherently to trigger false canonical trust
- Invariant to test: reported mainchain size and retained proof window must stay consistent under GC and reorgs
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
