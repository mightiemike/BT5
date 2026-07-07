### Title
Accumulated Fast-Withdrawal Fees Are Permanently Locked in `BaseWithdrawPool` With No Retrieval Path — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary
Every call to `submitFastWithdrawal` deducts a fee from the withdrawing user and credits it to `fees[productId]`. No function in `BaseWithdrawPool` or `WithdrawPool` ever reads that mapping to transfer the accumulated tokens out. The fee tokens are permanently held in the contract with no explicit, documented retrieval mechanism.

---

### Finding Description
`submitFastWithdrawal` is a public, permissionless function. When called, it computes a fee and either deducts it from the user's transfer amount (when `sendTo == msg.sender`) or pulls it from `msg.sender` via `safeTransferFrom`. In both cases the fee is credited to the in-contract accounting variable:

```solidity
fees[productId] += fee;   // BaseWithdrawPool.sol line 111
```

The actual ERC-20 tokens representing this fee remain in the `WithdrawPool` contract's balance. Searching the entire codebase, `fees[productId]` is **only ever incremented** — it is never read inside any function that performs a token transfer. There is no `claimFees`, `withdrawFees`, or equivalent function anywhere in `BaseWithdrawPool` or `WithdrawPool`. [1](#0-0) [2](#0-1) 

The only owner-callable escape hatch is `removeLiquidity`, which transfers an arbitrary caller-specified `amount` of a product's token to an arbitrary `sendTo` address:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
    external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [3](#0-2) 

`removeLiquidity` does **not** reference `fees[productId]` at all. It is a generic liquidity-drain function, not a fee-claiming function. Its use as a fee-recovery mechanism is entirely undocumented, and it operates on the raw ERC-20 balance rather than the tracked fee accounting. If the `WithdrawPool` address is ever rotated via `Clearinghouse.setWithdrawPool`, all previously accumulated fee tokens in the old pool are stranded unless the owner separately remembers to call `removeLiquidity` on the old contract. [4](#0-3) 

---

### Impact Explanation
Fast-withdrawal fees paid by every user who calls `submitFastWithdrawal` accumulate indefinitely in the `WithdrawPool` contract. Because no function consumes `fees[productId]` to transfer tokens, the fee revenue is effectively locked. If the pool is ever replaced, the stranded tokens become unrecoverable through any protocol-defined path. The `fees` mapping is a dead accounting variable that gives a false impression of fee tracking while providing no actual retrieval mechanism.

---

### Likelihood Explanation
`submitFastWithdrawal` is public and is the normal fast-withdrawal path for any user. Every fast withdrawal that charges a non-zero fee contributes to the locked balance. The condition is triggered by ordinary, unprivileged protocol usage and requires no special attacker action. [5](#0-4) 

---

### Recommendation
Add an explicit `claimFees(uint32 productId, address recipient)` function that reads `fees[productId]`, resets it to zero, and transfers the corresponding token amount to `recipient` (restricted to `onlyOwner` or a designated fee recipient). This makes the fee-recovery path explicit, auditable, and consistent with the accounting already tracked in `fees[productId]`. Additionally, document what happens to accumulated fees if `WithdrawPool` is replaced via `setWithdrawPool`.

---

### Proof of Concept

1. LP provides liquidity to `WithdrawPool` so it can service fast withdrawals.
2. User A calls `submitFastWithdrawal` with a valid signed withdrawal transaction where `sendTo == msg.sender`. The contract deducts `fee` from `transferAmount` and increments `fees[productId] += fee`. The fee tokens remain in the contract.
3. Repeat for many users over time. `fees[productId]` grows monotonically.
4. Owner decides to upgrade the pool and calls `Clearinghouse.setWithdrawPool(newPool)`.
5. All accumulated fee tokens in the old `WithdrawPool` are now stranded — no protocol function exists to claim them through the `fees` accounting path. The only recovery is an undocumented, out-of-band call to `removeLiquidity` on the old contract, which the protocol provides no guidance on. [6](#0-5) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L40-41)
```text
    mapping(uint32 => int128) public fees;

```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L750-753)
```text
    function setWithdrawPool(address _withdrawPool) external onlyOwner {
        require(_withdrawPool != address(0));
        withdrawPool = _withdrawPool;
    }
```
