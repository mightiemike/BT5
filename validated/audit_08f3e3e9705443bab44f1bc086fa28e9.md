### Title
Unrestricted `creditDeposit()` Allows Any Caller to Inflate `totalDepositsNormalized` and Suppress Protocol-Wide Interest Rates — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` carries no access control. Any address can send tokens (or ETH, which is auto-wrapped to WETH via `receive()`) to a deployed DDA and then call `creditDeposit()` to force-deposit those tokens into the DDA's fixed `subaccount`. Each such deposit increases `totalDepositsNormalized` in `SpotEngine`, which lowers the utilization ratio and suppresses the interest rate paid to all depositors across the affected product.

---

### Finding Description

`DirectDepositV1.creditDeposit()` is declared `external` with no `onlyOwner` or any other guard:

```solidity
// core/contracts/DirectDepositV1.sol  line 83
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        ...
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount, productId, uint128(balance), "-1"
            );
        }
    }
}
```

The `receive()` function (line 64) similarly accepts ETH from any caller and wraps it to WETH, leaving the WETH balance in the DDA ready for the next `creditDeposit()` call.

`depositCollateralWithReferral` in `Endpoint.sol` (line 123) is `public` and pulls tokens from `msg.sender` (the DDA) into the `Clearinghouse`, then enqueues a `DepositCollateral` slow-mode transaction. When the sequencer processes that transaction, `Clearinghouse.depositCollateral()` calls:

```solidity
// core/contracts/Clearinghouse.sol  line 207
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

`SpotEngineState._updateBalanceNormalized()` (line 15) then increments `state.totalDepositsNormalized`. The interest-rate engine in `_updateState()` (line 52) computes:

```solidity
// core/contracts/SpotEngineState.sol  line 64
int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

Artificially inflating `totalDeposits` drives `utilizationRatioX18` toward zero, collapsing `borrowerRateX18` toward `interestFloorX18` and, through the deposit-rate formula, collapsing the yield earned by every depositor in that product.

---

### Impact Explanation

An attacker who holds (or borrows) a meaningful quantity of a listed spot token can:

1. Transfer tokens directly to the DDA (ERC-20 transfer) or send ETH (auto-wrapped to WETH).
2. Call `creditDeposit()` — no signature, no permission required.
3. Force a deposit into the DDA's fixed `subaccount`, increasing `totalDepositsNormalized`.
4. After the slow-mode delay, the sequencer processes the transaction and the SpotEngine state is updated.
5. On the next `SpotTick`, `_updateState()` computes a lower utilization ratio and a lower deposit rate.

All existing depositors in the affected product receive less interest for the duration the inflated deposit remains. A large borrower can suppress their own borrow cost. The corrupted state variable is `totalDepositsNormalized` in `SpotEngineState.states[productId]`, which directly drives `cumulativeDepositsMultiplierX18` and thus every depositor's real balance.

---

### Likelihood Explanation

The entry path requires only a standard ERC-20 transfer to the DDA address followed by a public function call — no privileged role, no signature, no governance action. Any on-chain actor (trader, liquidator, or anonymous address) can execute this. The economic incentive exists for any large borrower who benefits from suppressed borrow rates. The DDA contract is deployed per-subaccount and its address is publicly discoverable from the `DirectDepositV1Created` event.

---

### Recommendation

Add an `onlyOwner` (or equivalent subaccount-owner) guard to `creditDeposit()`:

```solidity
function creditDeposit() external onlyOwner { ... }
```

Alternatively, restrict `receive()` to the `wrappedNative` contract only, so unsolicited ETH cannot be silently converted and queued for deposit. At minimum, document that the DDA is a trust-boundary and that any token balance it holds can be deposited by anyone.

---

### Proof of Concept

```
1. DDA deployed for subaccount S (fixed at construction, line 50).
2. Attacker calls USDC.transfer(dda_address, 10_000_000e6).
3. Attacker calls dda.creditDeposit().
   → DDA approves Endpoint for 10_000_000e6 USDC.
   → Endpoint.depositCollateralWithReferral(S, QUOTE_PRODUCT_ID, 10_000_000e6, "-1") is called.
   → Endpoint pulls USDC from DDA into Clearinghouse.
   → SlowModeTx enqueued: DepositCollateral{sender: S, productId: 0, amount: 10_000_000e6}.
4. After SLOW_MODE_TX_DELAY, sequencer calls executeSlowModeTransaction().
   → Clearinghouse.depositCollateral() → spotEngine.updateBalance(0, S, 10_000_000 * 1e12).
   → totalDepositsNormalized[QUOTE_PRODUCT_ID] increases by ~10_000_000e18.
5. Next SpotTick: _updateState() recomputes utilizationRatioX18 = totalBorrows / (totalDeposits + 10M).
   → borrowerRateX18 drops; depositRateMultiplierX18 drops.
   → All depositors' cumulativeDepositsMultiplierX18 accrues more slowly — permanent yield suppression
     until the inflated deposit is withdrawn (which requires the subaccount owner's action).
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
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

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L52-65)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
        int128 minDepositRateX18;
```

**File:** core/contracts/Endpoint.sol (L123-167)
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
        slowModeConfig = _slowModeConfig;
    }
```
