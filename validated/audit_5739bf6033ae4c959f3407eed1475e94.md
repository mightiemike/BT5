The vulnerability is clear. Let me verify the full deposit flow and the `BaseWithdrawPool` fast-withdrawal fee path in parallel.

### Title
Fee-on-Transfer Token Deposits Revert and Corrupt Subaccount Accounting — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs a two-hop token transfer: it pulls `amount` from the user into the Endpoint, then immediately forwards the same `amount` to the Clearinghouse. With a fee-on-transfer token, the Endpoint receives less than `amount` after the first hop, causing the second hop to revert. Even if the second hop were to succeed (e.g., due to pre-existing Endpoint balance), `Clearinghouse.depositCollateral` credits the full original `amount` to the subaccount rather than the actual received amount, inflating the user's on-chain balance beyond what the protocol holds.

---

### Finding Description

The deposit entry points `Endpoint.depositCollateral` and `Endpoint.depositCollateralWithReferral` both call `handleDepositTransfer` with the user-supplied `amount`: [1](#0-0) 

`handleDepositTransfer` in `EndpointStorage` performs two sequential transfers using the same `amount` value: [2](#0-1) 

- **Line 117**: `safeTransferFrom(token, from, amount)` — pulls `amount` from the user. If the token deducts a transfer fee, the Endpoint receives only `amount - fee_taken`.
- **Line 118**: `safeTransferTo(token, address(clearinghouse), amount)` — attempts to forward the full original `amount` to the Clearinghouse. Since the Endpoint only holds `amount - fee_taken`, this call reverts, blocking the entire deposit.

If the second transfer were to succeed (e.g., the Endpoint held a residual balance), the slow-mode queue entry encodes the original `amount`: [3](#0-2) 

When the sequencer later processes this entry, `Clearinghouse.depositCollateral` credits `txn.amount` (the original, inflated value) to the subaccount: [4](#0-3) 

The Clearinghouse's actual token balance is `amount - fee_taken`, but the SpotEngine balance is incremented by `amount`, creating a permanent solvency gap.

---

### Impact Explanation

**Primary impact — deposit revert (DoS):** Every deposit of a fee-on-transfer token reverts at the second `safeTransfer` in `handleDepositTransfer`. The deposit function becomes completely unavailable for any such token, regardless of deposit size or caller.

**Secondary impact — accounting inflation (solvency corruption):** If the second transfer succeeds (e.g., due to pre-existing Endpoint balance), the Clearinghouse credits the full `amount` to the subaccount while only holding `amount - fee_taken`. Repeated deposits compound this gap. The protocol becomes insolvent: the sum of all subaccount balances in the SpotEngine exceeds the actual token reserves held by the Clearinghouse. Withdrawals by later users will fail when the Clearinghouse runs out of real tokens.

---

### Likelihood Explanation

USDT — the most widely used stablecoin and a natural candidate for a trading protocol — has a fee-on-transfer mechanism that is currently set to zero but can be activated by the USDT owner at any time without notice. Any spot product configured with USDT (or any similar upgradeable token) as its collateral token would immediately trigger this vulnerability upon fee activation. No attacker action is required; the trigger is a standard token configuration change by the token issuer.

---

### Recommendation

Replace the two-hop fixed-amount pattern in `handleDepositTransfer` with a balance-snapshot approach:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    // Forward only what was actually received
    uint256 received = token.balanceOf(address(this));
    safeTransferTo(token, address(clearinghouse), received);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // Use actualReceived when encoding the slow-mode DepositCollateral tx
    // instead of the original `amount`
}
```

The slow-mode queue entry and the `Clearinghouse.depositCollateral` credit must both use the `actualReceived` value, not the user-supplied `amount`.

Alternatively, explicitly disallow fee-on-transfer tokens at product registration time by verifying that a round-trip transfer returns the exact input amount.

---

### Proof of Concept

1. A spot product is configured with a fee-on-transfer token (e.g., USDT with fees enabled, or any token with a 1% transfer fee).
2. User calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`.
3. `handleDepositTransfer` is invoked with `amount = 1000e6`.
4. `safeTransferFrom(token, user, 1000e6)` executes; the token deducts a 1% fee, so the Endpoint receives `990e6`.
5. `safeTransferTo(token, clearinghouse, 1000e6)` is called; the Endpoint only holds `990e6`, so this call reverts with an ERC20 insufficient-balance error.
6. The entire `depositCollateralWithReferral` transaction reverts. The user's deposit is rejected.
7. No subaccount is registered, no slow-mode entry is queued, and the user cannot interact with the protocol using this token.

Relevant code path:
- Entry: `Endpoint.depositCollateralWithReferral` [5](#0-4) 
- Root cause: `EndpointStorage.handleDepositTransfer` [2](#0-1) 
- Accounting credit (secondary path): `Clearinghouse.depositCollateral` [6](#0-5)

### Citations

**File:** core/contracts/Endpoint.sol (L123-148)
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

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Endpoint.sol (L155-164)
```text
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L193-208)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```
