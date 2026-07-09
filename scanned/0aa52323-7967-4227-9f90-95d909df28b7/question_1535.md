# Q1535: Recovery path mainchain-size implied history drift

## Question
Can an unprivileged attacker exploit the public state-transition surface while `headers_pool` still contains displaced fork headers that no longer have a mainchain height mapping, where the attacker can drive GC and reorg transitions until `get_mainchain_size` implies a history window that getters and proof APIs can no longer actually serve coherently, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_mainchain_size + contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::reorg_chain
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: drive GC and reorg transitions until `get_mainchain_size` implies a history window that getters and proof APIs can no longer actually serve coherently
- Invariant to test: reported mainchain size and retained proof window must stay consistent under GC and reorgs
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
