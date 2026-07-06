# Q3413: Shared key, index, or mapping-collision confusion

## Question
Can attacker-controlled identifiers reaching core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) collide in a shared mapping, bitmap, queue index, digest bucket, or derived storage key so that one user’s action overwrites, unlocks, or consumes another user’s state?

## Target
- File/function: core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Search for every derived storage key, bitmap slot, queue index, digest map, or hash bucket touched by core/contracts/Clearinghouse.sol / mintNlp(IEndpoint.MintNlp calldata txn, int128 oraclePriceX18, IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18); then try to construct two economically different actions that land on the same storage location.
- Invariant to test: Distinct users, subaccounts, orders, withdrawals, products, and queue items must never alias the same live state slot unless they are intentionally the same object.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or loss of funds through state-key collision.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
