### Title
Fast Withdrawal Fees Permanently Locked With No Claim Mechanism — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` collects real ERC20 tokens as fast-withdrawal fees and tracks them in `fees[productId]`. However, no function in `BaseWithdrawPool` or its concrete child `WithdrawPool` ever reads `fees[productId]` to distribute those tokens to a protocol treasury. The accounting variable grows indefinitely and the collected fee tokens have no dedicated claim path.

---

### Finding Description

Every call to `submitFastWithdrawal` computes a fee and either deducts it from the withdrawal amount or pulls it from `msg.sender` via `safeTransferFrom`. The fee is then added to the `fees[productId]` mapping: [1](#0-0) 

```solidity
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));
}

fees[productId] += fee;
```

The `fees` mapping is declared as a public state variable but is **never decremented or consumed** anywhere in the contract: [2](#0-1) 

The only token-exit function available to the owner is `removeLiquidity`, which transfers an arbitrary amount of a token to an address but **does not interact with `fees[productId]` at all**: [3](#0-2) 

```solidity
function removeLiquidity(
    uint32 productId,
    uint128 amount,
    address sendTo
) external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
```

`WithdrawPool`, the only concrete deployment of this base, adds no additional functions: [4](#0-3) 

There is no `claimFees`, `withdrawFees`, or equivalent function anywhere in the inheritance chain.

---

### Impact Explanation

Fast-withdrawal fee tokens accumulate inside `WithdrawPool` with no protocol-defined path to distribute them to a treasury. The `fees[productId]` accounting counter grows without bound and is never reset, so the on-chain state permanently misrepresents the claimable fee balance. While the owner can call `removeLiquidity` to recover tokens, this bypasses the fee accounting entirely, leaving `fees[productId]` permanently inflated and making it impossible to audit or automate fee distribution correctly.

**Severity: Medium.** Unlike the Perennial analog (where tokens were completely unrecoverable), `removeLiquidity` provides an owner escape hatch. However, the fee accounting is permanently broken and there is no clean, protocol-defined mechanism for fee distribution.

---

### Likelihood Explanation

Every `submitFastWithdrawal` call by any unprivileged caller accumulates fees. This is a normal, expected operation path. The locked accounting state is triggered unconditionally on every fast withdrawal.

---

### Recommendation

Add a dedicated `claimFees` function that reads `fees[productId]`, transfers the corresponding token amount to a protocol treasury address, and resets `fees[productId]` to zero:

```solidity
function claimFees(uint32 productId, address treasury) external onlyOwner {
    int128 amount = fees[productId];
    require(amount > 0, "No fees to claim");
    fees[productId] = 0;
    handleWithdrawTransfer(getToken(productId), treasury, uint128(amount));
}
```

---

### Proof of Concept

1. Any caller invokes `submitFastWithdrawal` with a valid verifier-signed withdrawal transaction.
2. `fees[productId] += fee` is executed at line 111 — real ERC20 tokens are now held by `WithdrawPool`.
3. Search the entire `BaseWithdrawPool` and `WithdrawPool` contracts for any function that reads `fees[productId]` and transfers tokens — none exists.
4. The owner can call `removeLiquidity(productId, amount, treasury)` to recover tokens, but `fees[productId]` remains permanently non-zero, breaking fee accounting. [5](#0-4) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L39-40)
```text
    // collected withdrawal fees in native token decimals
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
