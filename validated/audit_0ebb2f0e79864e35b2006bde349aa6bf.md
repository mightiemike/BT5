### Title
Fee-on-Transfer Token Deposit Inflates Clearinghouse Collateral Balance — (`File: core/contracts/EndpointStorage.sol`)

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` performs a two-leg token relay (`user → Endpoint → Clearinghouse`) using the caller-supplied `amount` for both legs and for the slow-mode transaction record. For fee-on-transfer tokens the Endpoint receives less than `amount` after the first leg, yet the second leg attempts to forward the full `amount` to the Clearinghouse. If the Endpoint holds a residual balance of that token (from slow-mode fees or prior deposits), the second leg silently drains those funds and the slow-mode queue records the inflated original `amount`. The Clearinghouse then credits the full `amount` to the depositor's spot balance, creating an unbacked collateral entry.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` calls `handleDepositTransfer` and then enqueues a slow-mode `DepositCollateral` transaction whose `amount` field is the raw caller-supplied value: [1](#0-0) 

`handleDepositTransfer` in `EndpointStorage.sol` performs two transfers with the same `amount`:

```solidity
safeTransferFrom(token, from, amount);              // user → Endpoint
safeTransferTo(token, address(clearinghouse), amount); // Endpoint → Clearinghouse
``` [2](#0-1) 

For a fee-on-transfer token the Endpoint receives only `amount - fee` after the first leg. The second leg then attempts to forward the full `amount`. If the Endpoint holds any residual balance of that token (accumulated from slow-mode fee charges or prior deposits), the shortfall is silently covered by those funds, and the call succeeds. The slow-mode queue entry records the original `amount`: [3](#0-2) 

When the sequencer later executes the slow-mode transaction, `Clearinghouse.depositCollateral` computes `amountRealized` directly from `txn.amount` (the inflated original) and credits it to the depositor's spot balance: [4](#0-3) 

No balance-before/after check is performed at any point in the deposit path.

---

### Impact Explanation

The depositor's on-chain collateral balance in the `SpotEngine` is inflated relative to the tokens actually held by the Clearinghouse. Repeated deposits with a fee-on-transfer token progressively widen the gap between recorded collateral and real token holdings. This corrupts the solvency invariant of the protocol: the depositor can open positions, borrow, or withdraw against collateral that was never fully backed, at the expense of other users whose tokens were drained from the Endpoint to cover the forwarding shortfall.

---

### Likelihood Explanation

Any unprivileged user can call `depositCollateral` or `depositCollateralWithReferral` directly. The trigger requires only that a fee-on-transfer token is listed as a supported product in the `SpotEngine`. The Endpoint accumulates residual balances from slow-mode fee charges (`chargeSlowModeFee`) and from the `DepositInsurance` path, making the second-leg drain realistic whenever such a token is active.

---

### Recommendation

Record the actual balance increase after `safeTransferFrom` and use that value for both the `safeTransferTo` and the slow-mode transaction record:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 actualAmount = token.balanceOf(address(this)) - balanceBefore;
    safeTransferTo(token, address(clearinghouse), actualAmount);
    // caller must use actualAmount (not amount) when building the slow-mode tx
}
```

Return `actualAmount` to the caller so that `depositCollateralWithReferral` encodes the real received amount into the `DepositCollateral` slow-mode transaction instead of the caller-supplied `amount`.

---

### Proof of Concept

1. A fee-on-transfer token (e.g., 1 % fee) is listed as `productId = 1` in the `SpotEngine`.
2. The Endpoint holds a residual balance of 10 units of that token (from prior slow-mode fee charges).
3. Attacker calls `depositCollateral("atk\x00…", 1, 1000)`.
4. `handleDepositTransfer` pulls 1000 from attacker; Endpoint receives 990 (fee = 10).
5. `safeTransferTo` forwards 1000 to Clearinghouse — the 10-unit shortfall is covered by the residual balance; call succeeds.
6. Slow-mode queue records `amount = 1000`.
7. Sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` credits `amountRealized = 1000 × multiplier` to attacker's spot balance.
8. Attacker's collateral is overstated by 10 units; the Endpoint's residual balance is now 0, drained to cover the gap. [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** core/contracts/EndpointStorage.sol (L95-119)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }

    function safeTransferTo(
        IERC20Base token,
        address to,
        uint256 amount
    ) internal virtual {
        token.safeTransfer(to, amount);
    }

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
