### Title
Liquidation Proceeds via Pure Internal Accounting While Collateral Deposit Requires Token Transfer, Preventing Users From Protecting Themselves — (File: `core/contracts/ClearinghouseLiq.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

In Nado, liquidation exclusively uses internal `updateBalance` accounting with no ERC-20 token transfer, while depositing collateral to improve subaccount health requires an actual `transferFrom` at the `Endpoint` layer. If the collateral token (e.g., USDC as the quote asset) is paused or blacklisted, users cannot deposit additional collateral to rescue their subaccount health, but the liquidation keeper can still liquidate them without any token movement.

---

### Finding Description

`ClearinghouseLiq._handleLiquidationPayment` settles the entire liquidation — for spot, perp, and spread positions — exclusively through `spotEngine.updateBalance` and `perpEngine.updateBalance` calls. No ERC-20 `transfer` or `transferFrom` is ever invoked during liquidation. [1](#0-0) 

For example, the perp-only branch:

```solidity
perpEngine.updateBalance(txn.productId, txn.liquidatee, -txn.amount, v.liquidationPayment);
perpEngine.updateBalance(txn.productId, txn.sender,    txn.amount, -v.liquidationPayment);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -v.liquidationFees);
```

No token ever moves. The entire settlement is a ledger update. [2](#0-1) 

By contrast, `Clearinghouse.depositCollateral` itself only calls `spotEngine.updateBalance` — the actual ERC-20 `transferFrom` that moves tokens from the user into the Clearinghouse happens at the `Endpoint` layer before `clearinghouse.depositCollateral` is invoked. If the token's `transferFrom` reverts (e.g., USDC paused, user blacklisted), the deposit fails entirely and the user's on-chain balance is never credited. [3](#0-2) 

Similarly, `withdrawCollateral` calls `handleWithdrawTransfer`, which performs a real `token.safeTransfer` to the `withdrawPool`. If the token is paused, this also reverts, blocking the user from withdrawing. [4](#0-3) [5](#0-4) 

The `ERC20Helper.safeTransfer` wrapper enforces that the call must succeed and return `true`; any failure causes a hard revert with `ERR_TRANSFER_FAILED`. [6](#0-5) 

The asymmetry is therefore:

| Action | Token Transfer Required | Reverts if Token Paused |
|---|---|---|
| `liquidateSubaccountImpl` | No — pure `updateBalance` | No |
| `depositCollateral` (Endpoint → Clearinghouse) | Yes — `transferFrom` at Endpoint | Yes |
| `withdrawCollateral` | Yes — `safeTransfer` to WithdrawPool | Yes |

---

### Impact Explanation

A user whose subaccount health is approaching the maintenance threshold cannot deposit additional USDC (or any paused collateral token) to improve their health, because the `transferFrom` at the Endpoint layer reverts. The liquidation keeper, however, faces no such barrier: `liquidateSubaccountImpl` requires only that `isUnderMaintenance` returns true, then settles entirely through `updateBalance`. The user is liquidated at a discount without any ability to self-rescue via collateral top-up. [7](#0-6) 

---

### Likelihood Explanation

USDC (Circle) has a well-documented on-chain pause and blacklist mechanism. A global pause of USDC — triggered by regulatory action, a Circle security incident, or a chain-level emergency — would simultaneously block all `depositCollateral` calls for the quote product while leaving liquidation fully operational. This is not a theoretical edge case; USDC pauses have occurred on other chains. Any market where USDC is the quote asset is affected.

---

### Recommendation

Add a circuit-breaker that disables liquidations for a given product when its token's `transfer`/`transferFrom` is non-functional. Concretely, before executing `_handleLiquidationPayment`, attempt a zero-value transfer of the collateral token and revert if it fails, or expose an admin/permissionless function (callable by anyone who can prove the transfer is broken) that pauses liquidations for the affected market. This mirrors the GMX team's own suggested mitigation.

---

### Proof of Concept

1. USDC is the quote token for a Nado market. Alice holds a leveraged perp position with health just above maintenance.
2. The USDC contract is paused (e.g., Circle emergency pause).
3. The market moves against Alice. Her maintenance health drops below zero.
4. Alice attempts to call `Endpoint.depositCollateral` with additional USDC to top up her margin. The call reverts because `USDC.transferFrom` reverts under the pause.
5. A liquidation keeper submits a `LiquidateSubaccount` transaction via `Endpoint.submitTransactions`.
6. `EndpointTx.processTransactionImpl` routes to `clearinghouse.liquidateSubaccount`, which `delegatecall`s `ClearinghouseLiq.liquidateSubaccountImpl`.
7. `_handleLiquidationPayment` executes: it calls only `spotEngine.updateBalance` / `perpEngine.updateBalance`. No USDC transfer occurs. The call succeeds.
8. Alice's position is liquidated at a discount. She had no on-chain recourse. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L447-570)
```text
    function _handleLiquidationPayment(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal {
        LiquidationVars memory v;
        address engine = txn.isEncodedSpread
            ? address(0)
            : address(productToEngine[txn.productId]);

        if (txn.isEncodedSpread) {
            uint32 spotId = txn.productId & 0xFFFF;
            uint32 perpId = txn.productId >> 16;
            (
                v.liquidationPriceX18,
                v.oraclePriceX18,
                v.oraclePriceX18Perp
            ) = getSpreadLiqPriceX18(spotId, perpId, txn.amount);

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);

            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            // transfer spot at the calculated liquidation price
            spotEngine.updateBalance(spotId, txn.liquidatee, -txn.amount);
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );
            spotEngine.updateBalance(spotId, txn.sender, txn.amount);
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );

            v.liquidationPayment = v.oraclePriceX18Perp.mul(txn.amount);
            perpEngine.updateBalance(
                perpId,
                txn.liquidatee,
                txn.amount,
                -v.liquidationPayment
            );

            perpEngine.updateBalance(
                perpId,
                txn.sender,
                -txn.amount,
                v.liquidationPayment
            );

            if (txn.amount < 0) {
                insurance = spotEngine.updateQuoteFromInsurance(
                    txn.liquidatee,
                    insurance
                );
            }
        } else if (engine == address(spotEngine)) {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );

            if (txn.amount < 0) {
                insurance = spotEngine.updateQuoteFromInsurance(
                    txn.liquidatee,
                    insurance
                );
            }
        } else {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );
            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
            perpEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount,
                v.liquidationPayment
            );
            perpEngine.updateBalance(
                txn.productId,
                txn.sender,
                txn.amount,
                -v.liquidationPayment
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationFees
            );
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
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

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
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

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
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
