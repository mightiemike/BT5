# Q8860: instrumentation bypass on deploy in global_contracts::apply_distribution_current_shard

## Question
Can an unprivileged attacker deploy attacker-controlled contract code through the standard transaction path that reaches `runtime/runtime/src/global_contracts.rs::apply_distribution_current_shard` with control over valid but adversarial Wasm structure, imports, and code layout and make nearcore prepare or instrument one code image while the executed image retains cheaper or unchecked behavior, breaking the invariant that the executed Wasm image must be exactly the metered and validated image, and leading to fee payment bypass?

## Target
- File/function: `runtime/runtime/src/global_contracts.rs::apply_distribution_current_shard`
- Entrypoint: deploy attacker-controlled contract code through the standard transaction path
- Attacker controls: valid but adversarial Wasm structure, imports, and code layout
- Exploit idea: prepare or instrument one code image while the executed image retains cheaper or unchecked behavior
- Invariant to test: the executed Wasm image must be exactly the metered and validated image
- Expected Immunefi impact: Fee payment bypass
- Fast validation: write a deploy-contract test with a crafted Wasm shape and assert the stored and executed code match the metered artifact
