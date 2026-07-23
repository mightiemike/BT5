# Q5754: crypto host-cost undercharge in traits::PartialEq

## Question
Can an unprivileged attacker call a contract method that uses exposed cryptographic host functionality that reaches `core/crypto/src/traits.rs::PartialEq` with control over bounded inputs to signature, hash, or curve operations and make nearcore perform more cryptographic work than the runtime charged for on a valid bounded input, breaking the invariant that cryptographic host operations must be metered for their full work and copied data, and leading to high: non-network-level dos?

## Target
- File/function: `core/crypto/src/traits.rs::PartialEq`
- Entrypoint: call a contract method that uses exposed cryptographic host functionality
- Attacker controls: bounded inputs to signature, hash, or curve operations
- Exploit idea: perform more cryptographic work than the runtime charged for on a valid bounded input
- Invariant to test: cryptographic host operations must be metered for their full work and copied data
- Expected Immunefi impact: High: non-network-level DoS
- Fast validation: write a bounded crypto-host test and assert gas exhaustion or early rejection occurs before disproportionate work completes
