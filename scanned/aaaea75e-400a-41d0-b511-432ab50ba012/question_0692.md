# Q692: Cross-contract desync of nlpLockedBalanceQueues

## Question
Can a normal user drive core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount) so that nlpLockedBalanceQueues is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Target the exact moment when core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount) mutates nlpLockedBalanceQueues and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through incorrect spot credit/debit or withdrawable-balance inflation.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.
