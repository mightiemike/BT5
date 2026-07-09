# Q3912: Recovery public pruning removes reorg anchor false canonical trust

## Question
Can an unprivileged attacker exploit the public state-transition surface after the fork point moves to the oldest retained canonical block, where the attacker can call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs, so that relayer recovery or downstream settlement trusts a branch that should no longer be canonical?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any normal relayer-recovery path that reacts to them
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs to trigger false canonical trust
- Invariant to test: public GC must not remove the only anchor needed to recover or validate the next canonical branch
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Replay the exact interleaving of public GC calls, relayer recovery queries, and a realistic short fork in a workspace test, then assert the targeted outcome never becomes reachable.
