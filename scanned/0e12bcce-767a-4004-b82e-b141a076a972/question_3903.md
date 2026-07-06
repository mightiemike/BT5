# Q3903: Proxy or helper authorization confusion

## Question
Can an unprivileged user reach core/contracts/Clearinghouse.sol / upgradeClearinghouseLiq(address _clearinghouseLiq) by confusing helper-address lookup, proxy-admin context, or delegatecall storage assumptions, thereby making a protected migration or upgrade effect appear authorized?

## Target
- File/function: core/contracts/Clearinghouse.sol / upgradeClearinghouseLiq(address _clearinghouseLiq)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Test every externally reachable path that feeds into helper-address resolution or upgrade-like selectors and confirm no attacker-controlled context can satisfy the auth check unexpectedly.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
