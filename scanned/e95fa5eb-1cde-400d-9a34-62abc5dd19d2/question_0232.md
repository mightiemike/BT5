# Q232: Nominal-versus-realized asset mismatch

## Question
Can core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) trust a nominal amount, preview amount, claimed amount, or signed amount that diverges from the assets actually transferred or the balances actually settled, creating unbacked credit or underpaid liabilities?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Compare the user-controlled nominal amount against the realized token movement, internal balance delta, and downstream settlement effect caused by core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs), especially around fees, wrappers, and non-standard token behavior.
- Invariant to test: Internal accounting must track realized asset movement and must not mint credit or settle liabilities from nominal amounts alone.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, underpayment, or hidden insolvency.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
