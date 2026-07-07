### Title
Fast Withdrawal Fees Permanently Locked in `BaseWithdrawPool` with No Claim Mechanism — (`core/contracts/BaseWithdrawPool.sol`)

### Summary

Fast withdrawal fees accumulated in the `fees[productId]` mapping inside `BaseWithdrawPool` are never decremented and have no dedicated claim function. The protocol has no on-chain path to retrieve these fees through the intended accounting mechanism. The only available escape hatch is `removeLiquidity`, a general-purpose owner function that does not update the `fees` mapping, leaving the accounting permanently corrupted.

### Finding Description

In `BaseWithdrawPool.submitFastWithdrawal`, every fast withdrawal charges a fee that is credited to the `fees[productId]` mapping: [1](#0-0) 

The fee is deducted from the withdrawal amount (or collected from the caller when `sendTo != msg.sender`) and the contract retains the ERC20 tokens. The `fees[productId]` mapping is the only on-chain record of how much has been collected per product.

However, there is no `claimFees` or equivalent function anywhere in `BaseWithdrawPool` or its concrete subclass `WithdrawPool`. The mapping is declared public and written to, but **never decremented**: [2](#0-1) 

The only function that moves tokens out of the contract is `removeLiquidity`: [3](#0-2) 

`removeLiquidity` is a general-purpose `onlyOwner` transfer that accepts an arbitrary `amount` and `sendTo` address. It does **not** read or decrement `fees[productId]`. This is directly analogous to the external report's `callExternalContract` escape hatch — a privileged, indirect path that bypasses the intended accounting entirely.

`WithdrawPool` adds no additional functions: [4](#0-3) 

### Impact Explanation

Every call to `submitFastWithdrawal` increments `fees[productId]` with no corresponding decrement path. The `fees` mapping is a public state variable that permanently diverges from the actual claimable balance after any `removeLiquidity` call. The protocol has no on-chain mechanism to claim fast withdrawal fees through the intended accounting path — the only retrieval route is the general-purpose `removeLiquidity`, which does not update `fees`, leaving the accounting state permanently corrupted. Any off-chain or on-chain system relying on `fees[productId]` to determine claimable protocol revenue will observe permanently inflated values.

### Likelihood Explanation

Every successful call to `submitFastWithdrawal` by any unprivileged user triggers the accounting corruption. No special conditions are required — the path is the normal fast withdrawal flow available to any user with a valid signed withdrawal transaction and sufficient token balance. [5](#0-4) 

### Recommendation

Add a dedicated `claimFees` function that transfers the accumulated fee balance to the owner and resets the `fees[productId]` counter:

```solidity
function claimFees(uint32 productId) external onlyOwner {
    int128 amount = fees[productId];
    require(amount > 0, "No fees to claim");
    fees[productId] = 0;
    handleWithdrawTransfer(getToken(productId), owner(), uint128(amount));
}
```

This ensures the `fees` mapping accurately reflects unclaimed fees at all times and provides a direct, auditable retrieval path for the protocol.

### Proof of Concept

1. Any user calls `submitFastWithdrawal` with a valid signed withdrawal transaction for `productId = 1` and `transferAmount = 1000`.
2. `fastWithdrawalFeeAmount` returns `fee = 5`.
3. `fees[1] += 5` — the contract now holds 5 tokens as protocol fee.
4. There is no `claimFees(1)` function to call.
5. The owner calls `removeLiquidity(1, 5, owner())` to retrieve the tokens — `fees[1]` remains `5` and is never decremented.
6. After N fast withdrawals, `fees[1]` reads as `5*N` even if all tokens have already been removed via `removeLiquidity`, permanently misrepresenting the claimable balance.

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
