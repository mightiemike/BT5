# Q2119: Arithmetic edge case in SLOW_MODE_TX_DELAY

## Question
Can attacker-controlled extremes of SLOW_MODE_TX_DELAY drive core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18)
- Entrypoint: User queues a transaction through Endpoint.submitSlowModeTransaction(...) and later executes it through Endpoint.executeSlowModeTransaction(...).
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Fuzz SLOW_MODE_TX_DELAY around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) mutates balances and risk state.
- Invariant to test: User-controlled calldata must not cause Endpoint to delegate into EndpointTx in a way that mutates unauthorized state.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Use a malicious token or callback-capable recipient to test whether Endpoint state mutates safely around external token movement and delegatecall paths.
