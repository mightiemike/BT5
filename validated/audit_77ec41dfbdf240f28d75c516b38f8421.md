### Title
Fast Withdrawal Fees Permanently Locked in `WithdrawPool` With No Fee Recipient Transfer Path - (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

In `BaseWithdrawPool.submitFastWithdrawal`, protocol fees are collected and accumulated in the `fees[productId]` mapping, but there is no function to transfer these fees to a fee recipient. The tokens remain locked in the contract indefinitely, with no dedicated withdrawal path.

---

### Finding Description

In `submitFastWithdrawal`, a fee is computed and the fee tokens are retained in the contract: [1](#0-0) 

When `sendTo == msg.sender`, the fee is deducted from `transferAmount` and the remaining tokens stay in the contract. When `sendTo != msg.sender`, the fee is pulled from the caller via `safeTransferFrom` into the contract. In both cases, `fees[productId] += fee` grows, but the fee tokens are never forwarded to any fee recipient address.

The only token-extraction function in the contract is `removeLiquidity`: [2](#0-1) 

This is `onlyOwner`, generic (not fee-specific), and critically does **not** decrement the `fees[productId]` mapping. There is no `claimFees`, `withdrawFees`, or `feeRecipient` mechanism anywhere in the contract or its sole concrete implementation `WithdrawPool`: [3](#0-2) 

---

### Impact Explanation

Protocol fees from every fast withdrawal are permanently locked in the `WithdrawPool` contract. The `fees[productId]` mapping grows monotonically but the corresponding tokens are never routed to a fee recipient. The protocol loses all fast-withdrawal fee revenue. **Impact: Medium** — direct, permanent loss of protocol fee revenue with no recovery path except a manual, untracked `removeLiquidity` call by the owner.

---

### Likelihood Explanation

`submitFastWithdrawal` is publicly callable with no access control. Every fast withdrawal that executes successfully accumulates fees in the contract. This is a structural omission that triggers on every single fast withdrawal. **Likelihood: High.**

---

### Recommendation

Add a dedicated fee withdrawal function that transfers the accumulated `fees[productId]` tokens to a configured fee recipient and resets the mapping:

```solidity
address public feeRecipient;

function withdrawFees(uint32 productId) external onlyOwner {
    int128 amount = fees[productId];
    require(amount > 0, "No fees");
    fees[productId] = 0;
    IERC20Base token = getToken(productId);
    token.safeTransfer(feeRecipient, uint256(amount));
}
```

---

### Proof of Concept

1. Alice calls `submitFastWithdrawal` with a valid signed withdrawal for `productId = 1`, `transferAmount = 1000e6 USDC`, and `sendTo = Alice`.
2. `fastWithdrawalFeeAmount` returns `fee = 5e6`.
3. `transferAmount` is reduced to `995e6` and sent to Alice. `fees[1] += 5e6`. The 5 USDC fee tokens remain in the `WithdrawPool` contract.
4. This repeats for every fast withdrawal. The `fees[1]` mapping grows, but no function exists to transfer these tokens to a fee recipient.
5. The only extraction path is `removeLiquidity(1, amount, owner)` — which is untracked, does not update `fees[1]`, and requires manual off-chain accounting to determine the correct fee amount. [4](#0-3) [5](#0-4)

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

**File:** core/contracts/WithdrawPool.sol (L15-19)
```text
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```
