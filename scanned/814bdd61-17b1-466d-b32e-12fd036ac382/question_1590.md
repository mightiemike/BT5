# Q1590: Recovery path getter split-brain around displaced tip

## Question
Can an unprivileged attacker exploit the public state-transition surface after the fork point moves to the oldest retained canonical block, where the attacker can observe whether getter calls can expose a displaced tip and a new canonical height mapping in separate transactions such that downstream settlement acts on a split-brain oracle, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::get_last_block_header + contract/src/lib.rs::get_last_block_height + contract/src/lib.rs::get_height_by_block_hash
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: observe whether getter calls can expose a displaced tip and a new canonical height mapping in separate transactions such that downstream settlement acts on a split-brain oracle
- Invariant to test: public getters must expose a coherent canonical view after reorg and GC transitions
- Expected Immunefi impact: Contract execution flows
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
