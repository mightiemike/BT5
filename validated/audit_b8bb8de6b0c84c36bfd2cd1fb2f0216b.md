### Title
Missing Sanctions Check in `submitFastWithdrawal` Allows Sanctioned Addresses to Bypass Withdrawal Restrictions — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The `submitFastWithdrawal` function in `BaseWithdrawPool` does not call `requireUnsanctioned` on the `sendTo` recipient address, while the slow-mode withdrawal path enforces this check for every submission. This allows a sanctioned address to receive collateral via the fast withdrawal path, directly bypassing the protocol's on-chain sanctions enforcement.

---

### Finding Description

The Nado protocol enforces a sanctions restriction via `requireUnsanctioned`, defined in `EndpointStorage`: [1](#0-0) 

This check is applied to **all slow-mode transaction submissions** in `submitSlowModeTransactionImpl`, including `WithdrawCollateral`: [2](#0-1) 

It is also applied to both the depositor and the depositee in `depositCollateralWithReferral`: [3](#0-2) 

However, the `submitFastWithdrawal` function — the alternative, user-callable withdrawal path — performs no sanctions check on either the `sendTo` recipient or the `msg.sender` caller: [4](#0-3) 

The `sendTo` address is resolved from the signed transaction and receives the full token transfer via `handleWithdrawTransfer`: [5](#0-4) 

The slow-mode `WithdrawCollateral` path enforces `validateSender` and routes `sendTo` as `address(0)` (defaulting to the sender's own address), so the `requireUnsanctioned(sender)` check at submission time covers the recipient: [6](#0-5) 

The fast path has no equivalent gate.

---

### Impact Explanation

A sanctioned address can receive collateral from the protocol by having any caller invoke `submitFastWithdrawal` with a valid sequencer-signed withdrawal transaction naming the sanctioned address as `sendTo`. The on-chain sanctions enforcement — which is applied consistently across deposits and slow-mode withdrawals — is entirely absent from this path. The asset delta is concrete: the sanctioned address receives the full `transferAmount` of the withdrawal token.

---

### Likelihood Explanation

The sequencer signs withdrawal transactions off-chain. A sanctioned address may have had a withdrawal signed before being added to the sanctions list, or the off-chain sanctions check may lag. Once a valid signed transaction exists, any caller can submit it via `submitFastWithdrawal` — there is no on-chain gate preventing the transfer to a sanctioned recipient. The function is `public` with no access control beyond signature verification.

---

### Recommendation

Add a `requireUnsanctioned` check on the resolved `sendTo` address inside `submitFastWithdrawal`, mirroring the enforcement applied in the slow-mode path:

```solidity
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

+   requireUnsanctioned(sendTo);   // mirror slow-mode enforcement

    // ... rest of function
}
```

`requireUnsanctioned` must be made accessible from `BaseWithdrawPool`, either by inheriting from `EndpointStorage` or by storing the `sanctions` list address directly in `BaseWithdrawPool`.

---

### Proof of Concept

1. Address `A` is added to the OFAC sanctions list (the `ISanctionsList` contract returns `true` for `A`).
2. The sequencer has previously signed a `WithdrawCollateral` or `WithdrawCollateralV2` transaction for address `A` with index `idx > minIdx`.
3. Any caller invokes `BaseWithdrawPool.submitFastWithdrawal(idx, transaction, signatures)`.
4. `requireValidTxSignatures` passes (valid sequencer signatures).
5. `sendTo` resolves to address `A`.
6. No `requireUnsanctioned` check is performed.
7. `handleWithdrawTransfer` transfers the full `transferAmount` to address `A`.
8. Address `A` receives protocol collateral despite being sanctioned, bypassing the restriction that blocks the same address from using the slow-mode withdrawal path.

### Citations

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L374-376)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
```

**File:** core/contracts/Endpoint.sol (L133-136)
```text
        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

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
