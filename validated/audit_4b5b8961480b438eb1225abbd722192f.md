### Title
Fast-Withdrawal Liquidity Deposits Are Permanently Locked Without Owner Intervention — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The `WithdrawPool` contract holds tokens deposited by liquidity providers (LPs) to service fast withdrawals. There is no mechanism for an LP to recover their deposited tokens. The sole removal path, `removeLiquidity()`, is gated behind `onlyOwner`. This is a structural analog to M-05: a deposit path exists for unprivileged actors, but the corresponding exit path is exclusively admin-controlled, permanently locking LP capital until the owner acts.

---

### Finding Description

The wiki documents the liquidity model explicitly:

> "Deposits: Tokens are typically transferred directly to the pool address."

An LP who wants to provide fast-withdrawal liquidity sends tokens directly to `WithdrawPool`. Once deposited, the only on-chain path to recover those tokens is `removeLiquidity()`:

```solidity
// BaseWithdrawPool.sol L151–157
function removeLiquidity(
    uint32 productId,
    uint128 amount,
    address sendTo
) external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
```

The `onlyOwner` modifier means no LP, trader, or unprivileged caller can invoke this function. The two other token-transfer paths in the contract — `submitWithdrawal()` (L116–132) and `submitFastWithdrawal()` (L81–114) — are designed exclusively to service user withdrawal requests routed through the `Clearinghouse` or verified by the `Verifier`. Neither path allows an LP to reclaim their own deposited principal.

The `fees` mapping (L40) accumulates fast-withdrawal fees per product. These fees also sit inside the pool and are equally unreachable by anyone other than the owner via `removeLiquidity()`.

There is no `depositLiquidity()` function, no LP share token, and no accounting of individual LP contributions. The contract has no mechanism to distinguish LP-deposited capital from protocol-owned capital, making it structurally impossible to add a permissionless LP exit without a redesign.

---

### Impact Explanation

Any LP who deposits tokens into `WithdrawPool` to earn fast-withdrawal fees has their principal permanently locked at the contract level. The only on-chain recovery path requires the owner to call `removeLiquidity()` and direct funds to an arbitrary `sendTo` address. If the owner is unresponsive, compromised, or acts adversarially, LP funds are irrecoverable. The owner also has unilateral discretion over *when* and *to whom* liquidity is returned, giving them complete custodial control over all LP capital in the pool.

---

### Likelihood Explanation

Any LP who participates in the fast-withdrawal system by sending tokens to `WithdrawPool` is immediately and unconditionally affected. The lock is not conditional on any edge case — it is the default state of every token balance held by the pool that was not deposited via the `Clearinghouse`'s `handleWithdrawTransfer()` path. The likelihood is high for any protocol deployment that relies on external LPs to seed fast-withdrawal liquidity.

---

### Recommendation

Implement a vault-style accounting model that tracks each LP's deposited share per product (e.g., a `mapping(address => mapping(uint32 => uint128)) lpDeposits`). Add a permissionless `withdrawLiquidity(uint32 productId, uint128 amount)` function that allows LPs to reclaim up to their recorded deposit, subject to available pool balance. This mirrors the judge's recommendation in M-05: transform the contract into something closer to a yield-bearing vault to allow easier adding and removing of liquidity without admin dependency.

---

### Proof of Concept

1. LP calls `token.transfer(address(withdrawPool), 1_000_000e6)` to seed fast-withdrawal liquidity for a USDC product.
2. LP later wishes to exit. They inspect `WithdrawPool` for a callable exit function.
3. `submitWithdrawal()` — requires `msg.sender == clearinghouse` (L122); reverts.
4. `submitFastWithdrawal()` — requires a valid verifier multi-sig over a user withdrawal transaction (L90–91); not applicable to LP principal recovery.
5. `removeLiquidity()` — `onlyOwner` (L155); reverts for the LP.
6. No other token-transfer function exists in `WithdrawPool` or `BaseWithdrawPool`.
7. LP's 1,000,000 USDC is locked until the owner calls `removeLiquidity(productId, 1_000_000e6, lpAddress)`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L39-42)
```text
    // collected withdrawal fees in native token decimals
    mapping(uint32 => int128) public fees;

    uint64 public minIdx;
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

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
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
