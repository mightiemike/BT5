# Q1611: Recovery path public pruning removes reorg anchor

## Question
Can an unprivileged attacker exploit the public state-transition surface while a proof for a previously canonical block is still economically relevant, where the attacker can call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: call `run_mainchain_gc` early enough to remove the last common ancestor that relayer recovery or a later heavier fork still needs
- Invariant to test: public GC must not remove the only anchor needed to recover or validate the next canonical branch
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
