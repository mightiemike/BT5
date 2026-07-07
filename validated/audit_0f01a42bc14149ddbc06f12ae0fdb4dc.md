### Title
Fee-on-Transfer Token Accounting Corruption in Deposit Flow — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs two ERC20 transfers using the caller-supplied `amount` without checking the actual balance increase. The same nominal `amount` is then recorded in the slow-mode deposit queue. When the sequencer executes the queued `DepositCollateral` transaction, `Clearinghouse.depositCollateral` credits the full nominal `amount` to the subaccount's spot balance, even though the Clearinghouse may have received less due to transfer fees. This inflates user collateral balances beyond the protocol's actual token holdings.

---

### Finding Description

The deposit entry point is `Endpoint.depositCollateralWithReferral`: [1](#0-0) 

It calls `handleDepositTransfer` with the raw caller-supplied `amount`, then immediately queues a slow-mode `DepositCollateral` transaction encoding that same `amount`: [2](#0-1) 

`handleDepositTransfer` executes two sequential transfers — first pulling `amount` from the depositor into the Endpoint, then forwarding `amount` onward to the Clearinghouse — with no balance snapshot before or after either call:

```solidity
safeTransferFrom(token, from, amount);          // step 1
safeTransferTo(token, address(clearinghouse), amount); // step 2
```

For a fee-on-transfer token, step 1 delivers only `amount - fee1` to the Endpoint. Step 2 then attempts to forward the full `amount` to the Clearinghouse. If the Endpoint holds residual balance of that token (e.g., from slow-mode fee collection in the same token, or from a prior partial deposit), step 2 silently drains that residual balance and the Clearinghouse receives `amount - fee2` net from this depositor. The slow-mode queue entry still records the original `amount`.

When the sequencer later executes the queued transaction, `Clearinghouse.depositCollateral` uses `txn.amount` — the original nominal value — to credit the subaccount: [3](#0-2) 

The `amountRealized` passed to `spotEngine.updateBalance` is derived entirely from `txn.amount`, not from any on-chain balance measurement. The protocol's internal accounting therefore diverges from its actual token holdings by the sum of transfer fees across all such deposits.

The same pattern exists in `EndpointTx.submitSlowModeTransactionImpl` for `DepositInsurance`, where `handleDepositTransfer` is called with `txn.amount` and the same queued value is later credited to `insurance` without a balance check: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Each deposit of a fee-on-transfer collateral token inflates the depositor's spot balance by the fee amount. Over time, the aggregate credited balances across all subaccounts exceed the Clearinghouse's actual token holdings. When users withdraw, the last withdrawers cannot be paid in full. The protocol becomes insolvent for that collateral token. Additionally, if the Endpoint holds residual balance of the token, step 2 of `handleDepositTransfer` silently drains funds belonging to other users or to the protocol's fee reserves.

---

### Likelihood Explanation

Any collateral token with a transfer fee (e.g., deflationary tokens, tokens with configurable fees, or tokens that activate fees post-listing) triggers this path. The entry point `depositCollateralWithReferral` is public and callable by any unprivileged user. No special permissions, governance capture, or admin keys are required. The attacker simply deposits a fee-on-transfer token that has been listed as a valid product in the spot engine.

---

### Recommendation

In `handleDepositTransfer`, record the Clearinghouse's token balance before and after the transfer and use the actual balance increase — not the nominal `amount` — as the value passed to the slow-mode queue:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 actualAmount) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balBefore = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    actualAmount = token.balanceOf(address(clearinghouse)) - balBefore;
}
```

`Endpoint.depositCollateralWithReferral` must then use the returned `actualAmount` when constructing the `DepositCollateral` slow-mode entry instead of the caller-supplied `amount`. [1](#0-0) 

---

### Proof of Concept

1. A fee-on-transfer token `FeeToken` (1% fee per transfer) is listed as a valid spot product with `productId = X`.
2. Attacker calls `Endpoint.depositCollateralWithReferral(subaccount, X, 1000e18, "")`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(FeeToken, attacker, Endpoint, 1000e18)` → Endpoint receives `990e18`.
   - `safeTransferTo(FeeToken, Endpoint, Clearinghouse, 1000e18)` → if Endpoint has ≥ `10e18` residual balance, Clearinghouse receives `990e18`; Endpoint's residual is drained by `10e18`.
4. Slow-mode queue records `DepositCollateral { sender: subaccount, productId: X, amount: 1000e18 }`.
5. Sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` calls `spotEngine.updateBalance(X, subaccount, 1000e18 * multiplier)`.
6. Attacker's subaccount is credited `1000e18` worth of collateral; Clearinghouse holds only `990e18`. The `10e18` discrepancy is a permanent accounting deficit, compounding with every such deposit. [2](#0-1) [6](#0-5) [7](#0-6)

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

**File:** core/contracts/Clearinghouse.sol (L193-209)
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
    }
```

**File:** core/contracts/Clearinghouse.sol (L261-266)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        insurance += amount;
```

**File:** core/contracts/EndpointTx.sol (L354-354)
```text
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
```
