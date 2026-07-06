# Q1564: Cross-contract desync of cumulativeFundingLongX18

## Question
Can a normal user drive core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance) so that cumulativeFundingLongX18 is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Target the exact moment when core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance) mutates cumulativeFundingLongX18 and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through incorrect PnL settlement or socialization.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.
