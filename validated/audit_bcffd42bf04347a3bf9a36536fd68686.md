### Title
Merkle Root Registration Without Token Balance Validation Allows Users to Expect Non-Existent Rewards — (File: `core/contracts/Airdrop.sol`)

---

### Summary
`registerMerkleRoot()` in `Airdrop.sol` accepts a Merkle root that encodes per-user reward amounts for a given week, but performs no check that the contract's token balance is sufficient to cover the total amounts encoded in that tree. This is the direct Nado analog of the external report: a Merkle-based distribution function that accepts off-chain-encoded reward commitments without validating on-chain solvency, leaving some users unable to claim rewards they are shown as entitled to.

---

### Finding Description
When the owner calls `registerMerkleRoot(week, merkleRoot)`, the function only enforces sequential week ordering and stores the root:

```solidity
function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
    external
    onlyOwner
{
    pastWeeks += 1;
    require(week == pastWeeks, "Invalid week provided.");
    merkleRoots[week] = merkleRoot;
}
``` [1](#0-0) 

There is no `totalAmount` parameter, no `IERC20(token).balanceOf(address(this))` check, and no invariant enforcing that the contract holds enough tokens to satisfy every leaf in the registered tree.

When a user calls `claim()`, the internal `_claim()` function calls `SafeERC20.safeTransfer` after proof verification:

```solidity
function _claim(uint32 week, uint256 totalAmount, bytes32[] calldata proof) internal {
    _verifyProof(week, msg.sender, totalAmount, proof);
    SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
    emit Claim(msg.sender, week, totalAmount);
}
``` [2](#0-1) 

If the contract is underfunded relative to the total encoded in the Merkle tree, `safeTransfer` reverts for claimants who arrive after the balance is exhausted. Because `claimed[week][sender] = totalAmount` is set inside `_verifyProof` before the transfer, a revert unwinds the entire transaction, so those users are not permanently locked out — but they remain unable to claim until the contract is refunded, which is entirely at the owner's discretion and not enforced on-chain. [3](#0-2) 

---

### Impact Explanation
Users who hold valid Merkle proofs for a registered week may be unable to claim their entitled token rewards if the contract's balance is insufficient to cover the full tree. Off-chain UIs that display pending rewards from the Merkle tree will show amounts that the contract cannot pay. This is a direct asset-availability failure: a user with a cryptographically valid entitlement receives a revert instead of tokens. The corrupted state is `IERC20(token).balanceOf(address(this))` falling below the sum of unclaimed leaf amounts for a registered week.

---

### Likelihood Explanation
Medium. The owner must either register a Merkle root before funding the contract, or encode a total that exceeds the deposited balance. The protocol's own acknowledged pattern ("we fill the contract with some reach and ETH to cover for any margin of error") confirms this is an operational risk that has already materialized in the analogous contracts. No attacker action is required — the failure mode is triggered by any underprivileged user calling `claim()` after the balance is exhausted.

---

### Recommendation
Add a `totalWeekAmount` parameter to `registerMerkleRoot()` and enforce a balance check at registration time:

```solidity
function registerMerkleRoot(uint32 week, bytes32 merkleRoot, uint256 totalWeekAmount)
    external
    onlyOwner
{
    pastWeeks += 1;
    require(week == pastWeeks, "Invalid week provided.");
    require(
        IERC20(token).balanceOf(address(this)) >= totalWeekAmount,
        "Insufficient balance for week."
    );
    merkleRoots[week] = merkleRoot;
}
```

If an exact total cannot be committed on-chain, at minimum document clearly in user-facing interfaces that displayed pending rewards are not guaranteed until the contract balance is verified, and add an off-chain monitoring alert that fires when `balanceOf(Airdrop) < sum_of_unclaimed_leaves`.

---

### Proof of Concept

1. Owner registers a Merkle root for week 1 encoding 1,000 tokens total across 10 users (100 tokens each), but the contract holds only 500 tokens.
   - `registerMerkleRoot(1, merkleRoot)` succeeds — no balance check. [1](#0-0) 

2. Users 1–5 call `claim()` with valid proofs. Each `safeTransfer` of 100 tokens succeeds. Contract balance reaches 0. [4](#0-3) 

3. Users 6–10 call `claim()` with equally valid proofs. `safeTransfer` reverts (ERC20 insufficient balance). Their transactions revert entirely, including the `claimed` state update. [3](#0-2) 

4. Users 6–10 are shown 100 tokens of pending rewards by any UI reading the Merkle tree, but cannot collect them. Recovery depends entirely on the owner depositing more tokens — there is no on-chain enforcement or deadline.

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
