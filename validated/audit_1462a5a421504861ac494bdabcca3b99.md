### Title
Fast Withdrawal Permanently Blocked for Blocklisted Token Recipients — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`submitFastWithdrawal` in `BaseWithdrawPool.sol` uses a push pattern to transfer tokens directly to `sendTo`. When the collateral token is USDC or USDT (which implement admin-controlled address block lists), and `sendTo` is a blocked address, every call to `submitFastWithdrawal` for that withdrawal index will revert. For `WithdrawCollateral` V1 transactions, `sendTo` is always derived from the signer's own address and cannot be overridden, making the fast withdrawal permanently unprocessable.

---

### Finding Description

`submitFastWithdrawal` resolves the destination address from the signed transaction and then pushes tokens to it via `handleWithdrawTransfer`:

```solidity
// BaseWithdrawPool.sol lines 81–114
function submitFastWithdrawal(
    uint64 idx,
    bytes calldata transaction,
    bytes[] calldata signatures
) public {
    require(!markedIdxs[idx], "Withdrawal already submitted");
    require(idx > minIdx, "idx too small");
    markedIdxs[idx] = true;                          // set before transfer

    Verifier v = Verifier(verifier);
    v.requireValidTxSignatures(transaction, idx, signatures);

    (uint32 productId, address sendTo, uint128 transferAmount)
        = resolveFastWithdrawal(transaction);
    ...
    handleWithdrawTransfer(token, sendTo, transferAmount);  // push to sendTo
}
```

`handleWithdrawTransfer` calls `token.safeTransfer(to, uint256(amount))` directly:

```solidity
// BaseWithdrawPool.sol lines 184–190
function handleWithdrawTransfer(IERC20Base token, address to, uint128 amount)
    internal virtual {
    token.safeTransfer(to, uint256(amount));
}
```

For `WithdrawCollateral` V1 transactions, `resolveFastWithdrawal` hardcodes `sendTo` to the signer's own Ethereum address with no override mechanism:

```solidity
// BaseWithdrawPool.sol lines 56–65
if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(...);
    return (
        signedTx.tx.productId,
        address(uint160(bytes20(signedTx.tx.sender))),  // always sender's address
        signedTx.tx.amount
    );
}
```

If `sendTo` is on a USDC/USDT block list, `token.safeTransfer` reverts. Because `markedIdxs[idx] = true` is set before the transfer, one might expect the index to be permanently consumed — but since the entire transaction reverts atomically, `markedIdxs[idx]` is also rolled back. The result is that every subsequent attempt to process this fast withdrawal also reverts. The withdrawal index is never marked, `minIdx` is never advanced past it, and the user's on-chain balance is never decremented — their funds remain locked inside the protocol with no fast-withdrawal exit path.

The same push pattern exists in `submitWithdrawal` (the sequencer-driven slow path), called from `Clearinghouse.handleWithdrawTransfer` → `Clearinghouse.withdrawCollateral`. However, the slow path can be mitigated by the user signing a `WithdrawCollateralV2` transaction with an explicit non-blocked `sendTo`. No such override exists for V1 fast withdrawals.

---

### Impact Explanation

**High.** The fast withdrawal functionality is permanently broken for any user whose address is on a USDC/USDT block list. No fast withdrawal provider can ever successfully process such a withdrawal. The user's collateral remains locked in the protocol with no fast-exit path. This is a direct corruption of a core protocol function (fast liquidity exit), not merely a degradation.

---

### Likelihood Explanation

**Low.** Two conditions must coincide: (1) the collateral token must implement an admin-controlled block list (e.g., USDC, USDT), and (2) the user's address must appear on that block list. Both conditions are realistic in production — USDC and USDT are the dominant stablecoins used as collateral in DeFi, and their issuers do actively maintain block lists.

---

### Recommendation

Apply the Pull over Push pattern to `submitFastWithdrawal`. Instead of pushing tokens to `sendTo` immediately, record the claimable amount in a mapping and let the recipient pull their funds in a separate transaction:

```solidity
mapping(address => mapping(uint32 => uint128)) public pendingWithdrawals;

function submitFastWithdrawal(...) public {
    ...
    pendingWithdrawals[sendTo][productId] += transferAmount;
}

function claimFastWithdrawal(uint32 productId) external {
    uint128 amount = pendingWithdrawals[msg.sender][productId];
    require(amount > 0);
    pendingWithdrawals[msg.sender][productId] = 0;
    handleWithdrawTransfer(getToken(productId), msg.sender, amount);
}
```

This ensures that a blocked `sendTo` address cannot cause the fast withdrawal submission to revert, and the fast withdrawal provider's transaction always succeeds.

---

### Proof of Concept

1. The protocol is configured with USDC as the collateral token for `productId = 1`.
2. Alice's address `0xAlice` is placed on USDC's admin block list.
3. Alice signs a `WithdrawCollateral` V1 transaction for `productId = 1`, `amount = 1000e6`.
4. A fast withdrawal provider calls `submitFastWithdrawal(idx, transaction, signatures)`.
5. `resolveFastWithdrawal` returns `sendTo = 0xAlice`.
6. `handleWithdrawTransfer(token, 0xAlice, 1000e6)` calls `USDC.transfer(0xAlice, 1000e6)`.
7. USDC reverts because `0xAlice` is blocked.
8. The entire `submitFastWithdrawal` call reverts; `markedIdxs[idx]` is rolled back.
9. Any subsequent call to `submitFastWithdrawal` with the same `idx` repeats steps 4–8 and always reverts.
10. Alice's funds remain locked in the protocol with no fast-withdrawal exit.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L56-65)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            return (
                signedTx.tx.productId,
                address(uint160(bytes20(signedTx.tx.sender))),
                signedTx.tx.amount
            );
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

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
