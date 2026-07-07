### Title
Missing Sanctions Validation on `sendTo` in `WithdrawCollateralV2` - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction type accepts a user-controlled `sendTo` address that is included in the EIP-712 digest and used to direct token transfers, but is never validated against the protocol's sanctions list. Every other fund-movement entry point in the protocol performs a `requireUnsanctioned` check on the destination address, but this check is absent for the `sendTo` field in `WithdrawCollateralV2`. A user can therefore withdraw protocol collateral directly to a sanctioned address by signing a `WithdrawCollateralV2` transaction with `sendTo` set to that address.

---

### Finding Description

**Root cause — missing format/content validation on a user-controlled address parameter:**

The external report's vulnerability class is: a parameter is accepted after a shallow type check but is never validated for valid content, leading to unexpected outcomes. The direct Nado analog is the `sendTo` field of `WithdrawCollateralV2`.

`WithdrawCollateralV2` is defined in `IEndpoint.sol`:

```solidity
struct WithdrawCollateralV2 {
    bytes32 sender;
    uint32 productId;
    uint128 amount;
    uint64 nonce;
    address sendTo;
    uint128 appendix; // Reserved for forward-compatible withdrawal features.
}
``` [1](#0-0) 

`sendTo` is an `address` — it is implicitly type-checked by the ABI decoder and is included in the EIP-712 digest. That is the only validation it receives. In `EndpointTx.processTransactionImpl`, the `WithdrawCollateralV2` branch (lines 437–465) validates the signature and fee bounds, then passes `sendTo` directly to `clearinghouse.withdrawCollateral`:

```solidity
clearinghouse.withdrawCollateral(
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    signedTx.tx.sendTo,   // ← never sanctions-checked
    nSubmissions
);
``` [2](#0-1) 

`Clearinghouse.withdrawCollateral` calls `handleWithdrawTransfer(token, sendTo, amount, idx)` with no intervening sanctions check: [3](#0-2) 

`handleWithdrawTransfer` in `EndpointStorage` resolves to a plain `token.safeTransfer(to, amount)`: [4](#0-3) 

**Contrast with every other fund-movement path:**

`depositCollateralWithReferral` explicitly calls `requireUnsanctioned` on both the depositor and the destination subaccount owner before any transfer:

```solidity
requireUnsanctioned(msg.sender);
requireUnsanctioned(sender);
``` [5](#0-4) 

`submitSlowModeTransactionImpl` calls `requireUnsanctioned(sender)` before queuing any slow-mode transaction: [6](#0-5) 

`requireUnsanctioned` is defined as:

```solidity
function requireUnsanctioned(address sender) internal view virtual {
    require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
}
``` [7](#0-6) 

The `WithdrawCollateralV2` path is the only fund-transfer path that never calls `requireUnsanctioned` on the destination address.

**Why the existing `allowLinkedSigner` guard does not help:**

The code sets `allowLinkedSigner = (signedTx.tx.sendTo == address(0))`, meaning when `sendTo` is non-zero the transaction must be signed by the subaccount owner directly, not a linked signer: [8](#0-7) 

This prevents a linked signer from redirecting funds, but it does not prevent the subaccount owner from deliberately directing funds to a sanctioned address. The owner signs the transaction themselves with `sendTo = <sanctioned_address>`, satisfying the signature check while bypassing the sanctions check entirely.

---

### Impact Explanation

A user can withdraw any amount of collateral they own directly to a sanctioned address. The protocol's `requireUnsanctioned` guard — which is enforced on every deposit and slow-mode submission — is completely bypassed for `WithdrawCollateralV2` withdrawals with a non-zero `sendTo`. The concrete asset delta is: up to the full collateral balance of the subaccount is transferred to a sanctioned address in a single transaction, with no protocol-level rejection.

---

### Likelihood Explanation

Medium. The attacker must be the subaccount owner (or have the owner's private key) and must deliberately set `sendTo` to a sanctioned address. No privileged protocol access is required. The `WithdrawCollateralV2` path is a standard, user-facing transaction type reachable by any trader. The sequencer must include the transaction, but the sequencer has no mechanism to detect that `sendTo` is sanctioned — that check was supposed to be in the contract.

---

### Recommendation

Add a `requireUnsanctioned` check on `sendTo` in the `WithdrawCollateralV2` processing branch of `EndpointTx.processTransactionImpl`, immediately before calling `clearinghouse.withdrawCollateral`:

```solidity
if (signedTx.tx.sendTo != address(0)) {
    requireUnsanctioned(signedTx.tx.sendTo);
}
```

This mirrors the pattern already used in `depositCollateralWithReferral` and `submitSlowModeTransactionImpl`.

---

### Proof of Concept

1. Attacker owns subaccount `S` with collateral balance `B` of token `T`.
2. Attacker signs a `WithdrawCollateralV2` transaction:
   - `sender = S`
   - `productId = <T's product ID>`
   - `amount = B`
   - `nonce = current nonce`
   - `sendTo = <sanctioned_address>`
   - `appendix = 0`
3. Attacker submits the signed transaction to the sequencer.
4. Sequencer calls `submitTransactionsChecked`, which calls `processTransaction` → `processTransactionImpl`.
5. `validateSignedTx` passes (signature is valid, nonce is correct).
6. Fee check passes (`feeX18 >= 0` and `<= currentFeeX18`).
7. `clearinghouse.withdrawCollateral(S, productId, B, <sanctioned_address>, nSubmissions)` is called.
8. `handleWithdrawTransfer(token, <sanctioned_address>, B, idx)` executes `token.safeTransfer(<sanctioned_address>, B)`.
9. Full balance `B` of token `T` is transferred to the sanctioned address. No `requireUnsanctioned` check was ever performed on `<sanctioned_address>`.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L97-104)
```text
    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }
```

**File:** core/contracts/EndpointTx.sol (L374-376)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
```

**File:** core/contracts/EndpointTx.sol (L442-448)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
```

**File:** core/contracts/EndpointTx.sol (L459-465)
```text
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/EndpointStorage.sol (L103-109)
```text
    function safeTransferTo(
        IERC20Base token,
        address to,
        uint256 amount
    ) internal virtual {
        token.safeTransfer(to, amount);
    }
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```

**File:** core/contracts/Endpoint.sol (L134-135)
```text
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```
