### Title
Deflationary Token Deposit Overcredits Subaccount Balance, Breaking Protocol Solvency - (File: `core/contracts/EndpointStorage.sol`)

### Summary
`handleDepositTransfer` in `EndpointStorage` performs two sequential ERC-20 transfers using the caller-supplied `amount` parameter without measuring the actual received amount. With a fee-on-transfer (deflationary) token, the Endpoint receives less than `amount` on the first transfer, then attempts to forward the full `amount` to the clearinghouse. The slow-mode queue records the original `amount`, and `Clearinghouse.depositCollateral` credits the subaccount with that same inflated figure — creating a solvency gap between the clearinghouse's real token holdings and the sum of all credited subaccount balances.

### Finding Description

The deposit entry point is `Endpoint.depositCollateralWithReferral`: [1](#0-0) 

It calls `handleDepositTransfer` with the raw caller-supplied `amount`, then enqueues a `DepositCollateral` slow-mode transaction also carrying that same `amount`.

`handleDepositTransfer` in `EndpointStorage` performs two transfers back-to-back, both using the original `amount`: [2](#0-1) 

- **Transfer 1** (`safeTransferFrom`): Endpoint receives `amount - fee` from the user.
- **Transfer 2** (`safeTransferTo`): Endpoint attempts to forward the full `amount` to the clearinghouse. If the Endpoint holds a pre-existing token balance (e.g., from accumulated slow-mode fees or prior deposits), this succeeds — consuming other users' funds to cover the shortfall. If no surplus exists, the call reverts.

When the slow-mode transaction is later processed, `Clearinghouse.depositCollateral` credits the subaccount with `txn.amount` — the original, uninflated figure — not the actual net amount the clearinghouse received: [3](#0-2) 

The `ERC20Helper.safeTransferFrom` wrapper performs no balance-before/after check and has no mechanism to detect a fee deduction: [4](#0-3) 

### Impact Explanation

For any spot product whose underlying token charges a transfer fee:

1. **Solvency corruption**: The clearinghouse's actual token balance is less than the aggregate of all credited subaccount balances. Users can withdraw more than the protocol holds, leaving later withdrawers unable to redeem.
2. **Cross-user fund theft**: When the Endpoint has a surplus balance, Transfer 2 silently draws on other depositors' tokens to cover the fee shortfall, directly stealing from them.
3. **Deposit DoS**: When no surplus exists, every deposit of that token reverts, making the product permanently unusable.

### Likelihood Explanation

Medium. Any unprivileged user can trigger this by calling `depositCollateral` or `depositCollateralWithReferral` with a product whose token is deflationary. No special role or governance action is required. The impact scales with the fee rate and total deposit volume.

### Recommendation

Measure the actual received amount using a balance-before/balance-after pattern inside `handleDepositTransfer`, and use that measured amount for both the clearinghouse transfer and the slow-mode `DepositCollateral.amount` field:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 actualReceived = token.balanceOf(address(this)) - balanceBefore;
    safeTransferTo(token, address(clearinghouse), actualReceived);
    // return actualReceived so the caller can queue the correct amount
}
```

The caller in `depositCollateralWithReferral` must then use `actualReceived` when constructing the `DepositCollateral` slow-mode transaction, so the clearinghouse credits only what it truly received.

Alternatively, restrict supported tokens to non-deflationary ones via an explicit allowlist check at product registration time.

### Proof of Concept

1. A spot product is registered with a deflationary token that charges a 1% transfer fee.
2. Attacker calls `Endpoint.depositCollateral(subaccountName, productId, 1000e18)`.
3. `handleDepositTransfer` fires:
   - Transfer 1: Endpoint receives `990e18` (1% fee deducted).
   - Transfer 2: Endpoint attempts to send `1000e18` to clearinghouse. If Endpoint holds ≥ `10e18` of that token from prior activity, the call succeeds — `10e18` is silently taken from other users.
4. The slow-mode queue records `amount = 1000e18`.
5. After the delay, `Clearinghouse.depositCollateral` credits the attacker's subaccount with `1000e18` (scaled to 18 decimals).
6. Clearinghouse's real balance is `990e18` (net of both fees), but the subaccount shows `1000e18` — a `10e18` phantom credit that can be withdrawn, draining the protocol.

### Citations

**File:** core/contracts/Endpoint.sol (L144-165)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
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
        });
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

**File:** core/contracts/Clearinghouse.sol (L205-208)
```text
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
