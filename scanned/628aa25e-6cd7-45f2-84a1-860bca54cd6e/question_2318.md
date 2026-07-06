# Q2318: Rounding leak through SLOW_MODE_TX_DELAY

## Question
Can repeated user-controlled updates around SLOW_MODE_TX_DELAY make core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18)
- Entrypoint: User waits for a signed batch that eventually reaches Endpoint.processTransaction(...) via the sequencer path.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving SLOW_MODE_TX_DELAY; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Deposits must only create protocol credit for value actually moved into protocol custody.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
