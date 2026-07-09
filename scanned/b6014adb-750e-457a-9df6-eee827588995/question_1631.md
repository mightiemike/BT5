# Q1631: Math path compact-target sign-bit work inflation

## Question
Can an unprivileged attacker exploit the reachable serialization or arithmetic path while the relayer parses raw header bytes from node RPC and forwards them into contract submission, where the attacker can choose compact targets near the sign-bit boundary and test whether a value that collapses to zero target locally inflates chainwork enough to skew fork choice, so that header validation, proof verification, or fork choice diverges from the source chain and creates a valid bounty impact?

## Target
- File/function: btc-types/src/utils.rs::target_from_bits + btc-types/src/utils.rs::work_from_bits + btc-types/src/u256.rs::inverse
- Entrypoint: public proof APIs, public GC plus recovery getters, or relayer-mediated header submission depending on which path reaches the target function
- Attacker controls: attacker-chosen proof bytes, header bytes, fork timing, or RPC-visible chain data under the normal production workflow
- Exploit idea: choose compact targets near the sign-bit boundary and test whether a value that collapses to zero target locally inflates chainwork enough to skew fork choice
- Invariant to test: compact-target parsing must never turn an invalid or degenerate target into exaggerated accumulated work
- Expected Immunefi impact: Cryptographic flaw
- Fast validation: Fuzz the boundary values in a unit or workspace test and compare every security-critical result against a reference implementation or an independently recomputed oracle.
