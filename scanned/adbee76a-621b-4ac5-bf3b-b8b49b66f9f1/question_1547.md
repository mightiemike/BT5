# Q1547: Recovery path oldest-retained proof freeze

## Question
Can an unprivileged attacker exploit the public state-transition surface immediately after `run_mainchain_gc` removes the first canonical height in storage, where the attacker can prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it, so that canonical history, relayer recovery, or downstream proof settlement enters a state that causes false trust or permanent freezing of funds?

## Target
- File/function: contract/src/lib.rs::run_mainchain_gc + contract/src/lib.rs::verify_transaction_inclusion_v2
- Entrypoint: public `run_mainchain_gc`, public getter calls, and any downstream component that reacts to them during normal relayer sync
- Attacker controls: the timing of `run_mainchain_gc`, observed getter outputs, and any attacker-controlled fork or proof that a normal relayer would later process
- Exploit idea: prune at the earliest legal moment so a deposit block that is still economically relied on loses its proof anchor before the bridge can verify it
- Invariant to test: public GC must not let a third party permanently freeze a valid pending bridge proof by deleting the earliest still-relevant anchor
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Reproduce the sequence in a workspace test by interleaving public GC calls, relayer recovery queries, and a realistic short fork, then assert canonical history and proof availability remain coherent.
