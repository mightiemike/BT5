# Q85: Cross-contract desync of claimed

## Question
Can a normal user drive core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) so that claimed is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Target the exact moment when core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) mutates claimed and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through duplicate or manipulated claims.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
