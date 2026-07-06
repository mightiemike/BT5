# Q3955: Pre-check versus post-effect mismatch

## Question
Can core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) satisfy an authorization, health, limit, or utilization check before a later effect changes the underlying balance or risk inputs, leaving the final state outside the condition that was actually checked?

## Target
- File/function: core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Locate every require/assert-style gate around core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx), then mutate the referenced balances, fees, or risk variables later in the same path and compare the checked pre-state to the committed post-state.
- Invariant to test: Safety checks must guard the final committed effect, not only an earlier intermediate state that becomes invalid before the transaction ends.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, liquidation bypass, or logic attack through check-effect mismatch.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.
