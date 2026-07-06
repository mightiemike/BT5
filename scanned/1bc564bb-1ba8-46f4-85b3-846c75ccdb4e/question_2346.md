# Q2346: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18)
- Entrypoint: User queues a transaction through Endpoint.submitSlowModeTransaction(...) and later executes it through Endpoint.executeSlowModeTransaction(...).
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: User-controlled calldata must not cause Endpoint to delegate into EndpointTx in a way that mutates unauthorized state.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
