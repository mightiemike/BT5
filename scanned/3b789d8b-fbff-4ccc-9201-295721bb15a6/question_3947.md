# Q3947: Hotspot-driven review path

## Question
Does the implementation detail noted for core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) create a reachable exploit path for an unprivileged attacker: Asset movement happens before spot balance update, utilization assertion, and health check.

## Target
- File/function: core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Translate the implementation note into an executable proof path and test whether the noted assumption breaks authorization, accounting, queue semantics, or settlement safety.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
