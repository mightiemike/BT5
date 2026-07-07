### Title
`submitFastWithdrawal()` Lacks Sanctions Check on `msg.sender` (Fast Withdrawal Provider) — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal()` is a public function callable by any address. It performs no `requireUnsanctioned` check on `msg.sender` (the fast withdrawal provider) or on `sendTo` (the recipient). The deposit path in `Endpoint.depositCollateralWithReferral()` explicitly checks both the depositor (`msg.sender`) and the subaccount owner (`sender`) against the sanctions list. The fast withdrawal path has no equivalent check, creating an asymmetric sanctions enforcement gap that a sanctioned address can exploit.

---

### Finding Description

`Endpoint.depositCollateralWithReferral()` enforces sanctions on both parties involved in a deposit:

```solidity
requireUnsanctioned(msg.sender);   // depositor
requireUnsanctioned(sender);       // subaccount owner
```

`BaseWithdrawPool.submitFastWithdrawal()` is the withdrawal-side counterpart. It is a `public` function with no access restriction and no sanctions check on any party:

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

    (uint32 productId, address sendTo, uint128 transferAmount) =
        resolveFastWithdrawal(transaction);
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

`msg.sender` here is the fast withdrawal provider — the party that initiates the action and either pays the fee (when `sendTo != msg.sender`) or self-receives the withdrawal net of fee (when `sendTo == msg.sender`). `sendTo` is the ultimate recipient of the collateral. Neither is screened.

`BaseWithdrawPool` does not inherit from `EndpointStorage` and holds no reference to the `ISanctionsList` contract, so the `requireUnsanctioned` helper is structurally absent from the entire withdrawal pool contract hierarchy.

The direct analog to the USDKG bug: just as `transferFrom` checked `_from` but not `msg.sender` (the spender), Nado's deposit path checks both parties while the fast withdrawal path checks neither — with `msg.sender` (the fast withdrawal provider, i.e., the "spender" role) being the missing check.

---

### Impact Explanation

A sanctioned address acting as `msg.sender` in `submitFastWithdrawal` can:

1. **Self-withdraw** (`sendTo == msg.sender`): receive collateral tokens directly from the `WithdrawPool` contract, bypassing the sanctions enforcement that would block them on the deposit path.
2. **Act as a fast withdrawal provider** (`sendTo != msg.sender`): pay fees into the protocol and facilitate withdrawals on behalf of other users, maintaining active protocol participation despite being sanctioned.

In the `sendTo` direction: an address that was sanctioned after the sequencer signed a withdrawal transaction can still receive funds via fast withdrawal, since `sendTo` is also unchecked. The sequencer may have signed the transaction before the address was added to the sanctions list, and the fast withdrawal provider can execute it at any time.

The corrupted invariant is: **the protocol's sanctions enforcement is asymmetric** — sanctioned addresses are blocked from depositing but not from withdrawing via the fast path, and sanctioned addresses can act as fast withdrawal providers.

---

### Likelihood Explanation

`submitFastWithdrawal` is a `public` function with no caller restriction. Any address, including a sanctioned one, can call it directly. The only prerequisite is a valid sequencer-signed withdrawal transaction and a valid `idx`. A sanctioned address that holds a prior approval or that self-withdraws (`sendTo == msg.sender`) has a direct, unprivileged path to execute this. Likelihood is **medium**: it requires a sanctioned address to have a valid signed withdrawal transaction, which is operationally plausible given the time gap between sequencer signing and on-chain execution.

---

### Recommendation

Add a `requireUnsanctioned` check on both `msg.sender` and `sendTo` inside `submitFastWithdrawal`. Since `BaseWithdrawPool` does not currently hold a reference to the `ISanctionsList` contract, the `WithdrawPool` initialization should accept a `_sanctions` address and store it, mirroring the pattern in `EndpointStorage`:

```solidity
function submitFastWithdrawal(
    uint64 idx,
    bytes calldata transaction,
    bytes[] calldata signatures
) public {
    // ... existing checks ...
    (uint32 productId, address sendTo, uint128 transferAmount) =
        resolveFastWithdrawal(transaction);

    requireUnsanctioned(msg.sender);  // fast withdrawal provider
    requireUnsanctioned(sendTo);      // recipient

    // ... rest of function ...
}
```

---

### Proof of Concept

1. Address `A` is added to the OFAC/Chainalysis sanctions list.
2. `A` previously submitted a `WithdrawCollateral` slow-mode transaction that was signed by the sequencer (valid `idx`, valid `signatures`).
3. `A` calls `WithdrawPool.submitFastWithdrawal(idx, transaction, signatures)` directly.
4. Because `sendTo == address(uint160(bytes20(signedTx.tx.sender))) == A` and `msg.sender == A`, the branch `sendTo == msg.sender` is taken.
5. `handleWithdrawTransfer(token, A, transferAmount - fee)` executes, transferring collateral tokens to `A`.
6. No sanctions check was performed at any point in `BaseWithdrawPool`. The sanctioned address successfully withdrew collateral.

Alternatively, `A` acts as a third-party fast withdrawal provider (`sendTo != msg.sender`), paying the fee and facilitating another user's withdrawal, maintaining active protocol interaction despite being sanctioned. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/contracts/Endpoint.sol (L123-135)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

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

**File:** core/contracts/WithdrawPool.sol (L15-19)
```text
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```
