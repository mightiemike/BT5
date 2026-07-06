# Q2812: Beneficiary routing default or zero-value coercion

## Question
Can core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx) fall back to a default recipient, default subaccount, zero address, or caller-derived beneficiary in a way that lets the attacker redirect value or settle against the wrong destination without explicitly authorizing it?

## Target
- File/function: core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx)
- Entrypoint: User calls Endpoint.depositCollateral(...) directly.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Force optional recipient fields, empty sendTo values, zero subaccounts, unset isolated mappings, or caller-derived defaults around core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx) and compare who ultimately receives value or state updates.
- Invariant to test: Every value-moving action must resolve to exactly one intended beneficiary and must not silently substitute a different account or recipient.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, transfer, or account mutation through beneficiary confusion.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
