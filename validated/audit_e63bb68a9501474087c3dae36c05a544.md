### Title
Fast Withdrawal Fees Permanently Locked in `WithdrawPool` — No Transfer Mechanism to Fee Recipient - (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` calculates and collects fast withdrawal fees from users, accumulating them in the `fees[productId]` mapping. However, no function exists anywhere in `BaseWithdrawPool` or `WithdrawPool` to transfer these accumulated fees to any recipient. The fees are permanently locked in the contract.

---

### Finding Description

In `submitFastWithdrawal`, a fee is computed and either deducted from the user's withdrawal amount or pulled from `msg.sender` directly:

```solidity
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));
}

fees[productId] += fee;
``` [1](#0-0) 

In both branches, the fee value is retained inside the `WithdrawPool` contract and credited to `fees[productId]`. The `fees` mapping is declared as:

```solidity
mapping(uint32 => int128) public fees;
``` [2](#0-1) 

A full audit of `BaseWithdrawPool.sol` and `WithdrawPool.sol` reveals **no function that reads `fees[productId]` for the purpose of transferring or distributing those fees**. The only other function that moves tokens out is `removeLiquidity`, which is `onlyOwner` and operates on arbitrary amounts with no reference to `fees`:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
    external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [3](#0-2) 

`WithdrawPool` adds no new logic beyond calling `_initialize`: [4](#0-3) 

The `fees` mapping is a write-only dead end. Every fast withdrawal permanently locks a portion of user funds in the contract with no recovery path for the intended fee recipient.

---

### Impact Explanation

**Impact: Medium.**

Every call to `submitFastWithdrawal` causes real ERC-20 tokens to be retained in the `WithdrawPool` contract under `fees[productId]`, with no on-chain mechanism to distribute them. The fee recipient (protocol treasury or sequencer) never receives these funds. Over time, the locked amount grows proportionally to fast withdrawal volume. The tokens are not lost in the sense of being burned, but they are inaccessible to any designated recipient through the contract's own interface — constituting a concrete, measurable asset accounting corruption.

---

### Likelihood Explanation

**Likelihood: Medium.**

`submitFastWithdrawal` is a publicly callable function reachable by any user who holds a valid signed withdrawal transaction. It is a core fast-path feature of the protocol. Every invocation silently locks fees. No special conditions, admin access, or unusual state are required to trigger the issue — it fires on every normal fast withdrawal. [5](#0-4) 

---

### Recommendation

Add a privileged `withdrawFees` function (e.g., `onlyOwner`) that reads `fees[productId]`, resets it to zero, and transfers the accumulated amount to a designated fee recipient address:

```solidity
function withdrawFees(uint32 productId, address recipient) external onlyOwner {
    uint128 amount = uint128(fees[productId]);
    fees[productId] = 0;
    handleWithdrawTransfer(getToken(productId), recipient, amount);
}
```

Alternatively, transfer the fee directly to the recipient inside `submitFastWithdrawal` rather than accumulating it, mirroring the pattern used in the rest of the protocol.

---

### Proof of Concept

1. Alice calls `submitFastWithdrawal` with a valid signed `WithdrawCollateral` transaction for 1000 USDC on `productId = 1`.
2. `fastWithdrawalFeeAmount` returns `fee = 5` (native decimals).
3. Since `sendTo == msg.sender`, `transferAmount` becomes `995` and `fees[1] += 5`.
4. Alice receives 995 USDC. The `WithdrawPool` contract retains 5 USDC in its ERC-20 balance, tracked by `fees[1]`.
5. No function in `BaseWithdrawPool` or `WithdrawPool` can transfer this 5 USDC to any fee recipient. The value is permanently locked unless the owner calls `removeLiquidity` with a manually computed amount — which bypasses the `fees` accounting entirely and provides no structured fee-distribution guarantee. [1](#0-0)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L40-40)
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
