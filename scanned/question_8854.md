# Q8854: instrumentation bypass on deploy in function_call::action_function_call

## Question
Can an unprivileged attacker deploy attacker-controlled contract code through the standard transaction path that reaches `runtime/runtime/src/function_call.rs::action_function_call` with control over valid but adversarial Wasm structure, imports, and code layout and make nearcore prepare or instrument one code image while the executed image retains cheaper or unchecked behavior, breaking the invariant that the executed Wasm image must be exactly the metered and validated image, and leading to fee payment bypass?

## Target
- File/function: `runtime/runtime/src/function_call.rs::action_function_call`
- Entrypoint: deploy attacker-controlled contract code through the standard transaction path
- Attacker controls: valid but adversarial Wasm structure, imports, and code layout
- Exploit idea: prepare or instrument one code image while the executed image retains cheaper or unchecked behavior
- Invariant to test: the executed Wasm image must be exactly the metered and validated image
- Expected Immunefi impact: Fee payment bypass
- Fast validation: write a deploy-contract test with a crafted Wasm shape and assert the stored and executed code match the metered artifact
