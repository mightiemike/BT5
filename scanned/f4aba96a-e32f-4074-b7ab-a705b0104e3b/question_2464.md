# Q2464: Partial batch progress without full rollback

## Question
Can a loop, queue, or multi-step batch around core/contracts/Endpoint.sol / submitSlowModeTransaction(bytes calldata transaction) make economic progress on early items even though a later item fails, leaving fill state, claim state, fees, or balances inconsistent with an all-or-nothing user assumption?

## Target
- File/function: core/contracts/Endpoint.sol / submitSlowModeTransaction(bytes calldata transaction)
- Entrypoint: User calls Endpoint.depositCollateral(...) directly.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Construct a mixed-validity batch or queue sequence through core/contracts/Endpoint.sol / submitSlowModeTransaction(bytes calldata transaction), force one later element to fail, and compare whether earlier state changes remain committed in a way that can be exploited or replayed.
- Invariant to test: Batched or queued user actions must either preserve consistent partial-progress rules or prevent attackers from extracting value from early-commit and late-fail combinations.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through inconsistent partial progress handling.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
