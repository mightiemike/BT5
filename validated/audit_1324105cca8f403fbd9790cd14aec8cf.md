### Title
`submitFastWithdrawal` Bypasses Sanctions Check on `sendTo`, Preventing Protocol Owner from Blocking Transfers to Sanctioned Addresses - (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary
The `submitFastWithdrawal` function in `BaseWithdrawPool.sol` transfers tokens to a caller-controlled `sendTo` address without checking whether that address is sanctioned. The protocol enforces an on-chain sanctions check in `depositCollateralWithReferral` but omits it in `submitFastWithdrawal`, meaning the protocol owner cannot prevent asset transfers to sanctioned addresses via the fast-withdrawal path even after adding them to the sanctions list.

---

### Finding Description

`submitFastWithdrawal` is a public function that resolves a `sendTo` address from a signed withdrawal transaction and immediately transfers tokens to it: [1](#0-0) 

The `sendTo` address is resolved from the transaction payload without any sanctions check: [2](#0-1) 

For `WithdrawCollateralV2`, `sendTo` is an arbitrary address supplied by the user in the signed transaction — it need not match the subaccount owner: [3](#0-2) 

By contrast, `depositCollateralWithReferral` in `Endpoint.sol` explicitly checks both the depositor and the subaccount owner against the sanctions list before any asset movement: [4](#0-3) 

The `requireUnsanctioned` guard is defined in `EndpointStorage.sol` and calls the external `ISanctionsList`: [5](#0-4) 

`BaseWithdrawPool` has no reference to the sanctions contract and performs no equivalent check before calling `handleWithdrawTransfer`: [6](#0-5) 

---

### Impact Explanation

A user can sign a `WithdrawCollateralV2` transaction specifying any arbitrary address as `sendTo`. Once the verifier co-signs the transaction (verifying only signature validity and index ordering, not sanctions status), any caller can invoke `submitFastWithdrawal` to transfer tokens to that address. If the `sendTo` address is sanctioned — whether it was sanctioned at signing time or becomes sanctioned afterward — the protocol owner has no on-chain mechanism to block the transfer, because `submitFastWithdrawal` never consults the sanctions list. This directly mirrors the FraxPoolV3 pattern: a privileged safety control (sanctions list) exists and can be updated by the owner, but the asset-transfer function bypasses it entirely, eliminating the owner's ability to limit the scope of a compliance incident or exploit.

---

### Likelihood Explanation

Medium. The `WithdrawCollateralV2` transaction type explicitly supports a user-specified `sendTo` that differs from the subaccount owner. A user wishing to route funds to a sanctioned address (e.g., a mixer, a newly-sanctioned counterparty, or their own address after being sanctioned) can sign such a transaction before the sanctions event and have it executed via fast withdrawal afterward. No privileged access is required beyond a valid user signature and verifier co-signature.

---

### Recommendation

Add a sanctions check on the resolved `sendTo` address inside `submitFastWithdrawal` before calling `handleWithdrawTransfer`. `BaseWithdrawPool` should store a reference to the `ISanctionsList` contract (or receive it via the `clearinghouse`) and call `require(!sanctions.isSanctioned(sendTo))` after resolving the recipient. This mirrors the defense-in-depth pattern already applied in `depositCollateralWithReferral`.

---

### Proof of Concept

1. User Alice signs a `WithdrawCollateralV2` transaction with `sendTo = sanctioned_address` (an address on the OFAC/Chainalysis sanctions list).
2. The verifier co-signs the transaction (verifying only the user signature and index).
3. Alice's address is subsequently added to the sanctions list, or `sanctioned_address` was always sanctioned.
4. Any caller invokes `submitFastWithdrawal(idx, transaction, signatures)`.
5. `resolveFastWithdrawal` returns `sendTo = sanctioned_address`.
6. `handleWithdrawTransfer` transfers tokens to `sanctioned_address` — no sanctions check is performed.
7. The protocol owner cannot prevent this transfer by updating the sanctions list, because `submitFastWithdrawal` never reads it.

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
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

**File:** core/contracts/Endpoint.sol (L133-135)
```text
        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
