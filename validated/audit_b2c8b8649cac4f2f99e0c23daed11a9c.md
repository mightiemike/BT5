### Title
Airdrop Contract Can Over-Commit Token Distributions With No Balance Solvency Check, Causing All Claims to Fail — (File: `core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop.sol` allows the owner to register weekly Merkle roots that commit to arbitrary token distribution amounts with no check that the contract holds sufficient tokens to cover all outstanding commitments. Once the contract's token balance is exhausted, every subsequent `claim()` call reverts at the `safeTransfer` step, permanently blocking all users from claiming any rewards — including for weeks that were legitimately funded.

---

### Finding Description

`registerMerkleRoot` registers a new weekly distribution commitment by storing a Merkle root: [1](#0-0) 

There is no accounting of the cumulative token amount committed across all registered weeks, and no check that `IERC20(token).balanceOf(address(this))` is sufficient to cover the newly committed week's total distribution plus all prior unclaimed amounts.

When a user calls `claim()`, it iterates over `ClaimProof[]` entries and for each calls `_claim`, which calls `_verifyProof` then immediately executes a token transfer: [2](#0-1) 

The `safeTransfer` at line 71 will revert if the contract's token balance is insufficient. Because `claim()` processes all proofs in a single loop with no partial-success handling: [3](#0-2) 

a single failing transfer reverts the entire transaction. Once the contract's balance is exhausted, **no user can claim any reward for any week**, even weeks that were properly funded.

The `claimed` mapping is updated inside `_verifyProof` before the transfer: [4](#0-3) 

This means a user whose claim reverts due to insufficient balance has their `claimed[week][sender]` state set to `totalAmount` before the revert unwinds it — but since the whole transaction reverts, the state is not persisted. However, the user must retry, and if the contract remains underfunded, they are permanently locked out.

---

### Impact Explanation

All users are unable to claim their airdrop rewards once the contract's token balance is exhausted. The `claim()` function is all-or-nothing: a single `safeTransfer` revert blocks the entire batch. Users who have valid Merkle proofs for legitimately registered weeks cannot receive their tokens. The committed reward obligations grow unboundedly as new weeks are registered, with no on-chain enforcement that the contract is solvent.

---

### Likelihood Explanation

The owner registers Merkle roots in good faith on a weekly cadence. If the contract is not topped up with tokens before each `registerMerkleRoot` call, or if the total committed amount across all weeks exceeds the deposited balance (e.g., due to operational error, delayed funding, or a miscalculation of unclaimed balances from prior weeks), the contract becomes insolvent. This is a realistic operational scenario requiring no malicious actor — only a funding gap between root registration and token deposit.

---

### Recommendation

1. Track the total committed but unclaimed token amount across all weeks in a state variable (e.g., `totalCommitted`).
2. In `registerMerkleRoot`, require that `IERC20(token).balanceOf(address(this)) >= totalCommitted + newWeekTotalAmount` before accepting the new root, and transfer the required tokens from the caller at registration time.
3. Alternatively, require the caller to deposit the exact week's token amount alongside `registerMerkleRoot`, atomically funding each commitment at registration.

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root1)` committing 1,000,000 tokens for week 1. Contract holds 800,000 tokens.
2. Users begin claiming week 1. After 800,000 tokens are claimed, the contract balance reaches 0.
3. Any subsequent call to `claim([ClaimProof{week: 1, totalAmount: X, proof: ...}])` reaches line 71 (`safeTransfer`) and reverts with an ERC20 insufficient-balance error.
4. All remaining week-1 claimants — and all future week claimants — are permanently blocked from receiving their rewards.
5. Owner calls `registerMerkleRoot(2, root2)` committing another 500,000 tokens for week 2 with no balance check. The contract remains at 0 balance, and week-2 claims also immediately fail. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }
```

**File:** core/contracts/Airdrop.sol (L62-62)
```text
        claimed[week][sender] = totalAmount;
```

**File:** core/contracts/Airdrop.sol (L65-73)
```text
    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }
```

**File:** core/contracts/Airdrop.sol (L75-83)
```text
    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }
```
