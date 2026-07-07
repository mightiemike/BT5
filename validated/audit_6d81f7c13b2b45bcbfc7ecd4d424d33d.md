### Title
Same-Wallet Cross-Subaccount Self-Liquidation Bypasses `txn.sender != txn.liquidatee` Check — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`ClearinghouseLiq::liquidateSubaccountImpl` guards against self-liquidation with a single `bytes32` identity comparison. Because Nado subaccounts are `bytes32` values that encode `wallet_address (20 bytes) + name (12 bytes)`, one wallet can own arbitrarily many distinct subaccounts. The check passes trivially when the liquidator and liquidatee are different subaccounts of the same wallet, allowing a user to self-liquidate and capture the liquidation discount while the insurance fund absorbs any shortfall.

---

### Finding Description

`liquidateSubaccountImpl` enforces:

```solidity
require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
``` [1](#0-0) 

`txn.sender` and `txn.liquidatee` are both `bytes32` subaccount identifiers. [2](#0-1) 

Nado subaccounts are structured as `address(20 bytes) ++ name(12 bytes)`. A single wallet `W` can register and sign for any number of subaccounts — e.g., `W+"default"` and `W+"liq"` — because signature validation accepts any signer whose recovered address equals the first 20 bytes of the `bytes32` sender, or its linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The `LiquidateSubaccount` transaction is signed by the liquidator subaccount and relayed by the sequencer. The sequencer performs no on-chain wallet-level identity check; the only on-chain guard is the `bytes32` inequality above. [4](#0-3) 

**Attack path:**

1. Wallet `W` opens subaccount `A = W+"default"` and takes on a large leveraged perp or spot position.
2. Wallet `W` opens subaccount `B = W+"liq"` with sufficient quote collateral to act as liquidator.
3. Market movement (or a deliberate under-collateralization via a separate mechanism) pushes subaccount `A` below maintenance margin.
4. `W` signs a `LiquidateSubaccount` transaction: `sender = B`, `liquidatee = A`.
5. The sequencer relays it. On-chain: `B != A` → check passes.
6. `_handleLiquidationPayment` transfers the position from `A` to `B` at `liquidationPriceX18` (below oracle). `B` pays `liquidationPayment + liquidationFees`; `A` receives `liquidationPayment`. The discount `(oraclePrice − liquidationPrice) × (1 − FEE_FRACTION)` accrues to `B`. [5](#0-4) 

If `A`'s quote balance is negative after the transfer, the insurance fund covers the shortfall via `spotEngine.socializeSubaccount` or `updateQuoteFromInsurance`. [6](#0-5) 

---

### Impact Explanation

The attacker captures the liquidation discount (the economic incentive designed to attract external liquidators) without providing the service of monitoring and liquidating risky third-party positions. When the position is deeply underwater, the insurance fund absorbs the residual shortfall, constituting a direct drain of protocol reserves. Repeated execution depletes the insurance fund and degrades the protocol's ability to cover future bad debt, ultimately socializing losses to all participants.

---

### Likelihood Explanation

**Medium.** The attacker must wait for (or engineer) a maintenance-margin breach in subaccount `A` — this requires real price movement or a separate mechanism to reduce collateral. Unlike the Licredity atomic case, this cannot be done in a single transaction. However, no sequencer compromise is required: the attacker simply submits a validly signed `LiquidateSubaccount` transaction from their own wallet for their own liquidatee subaccount. Any user with an underwater position and a second funded subaccount can execute this. The `linkedSigners` mechanism further widens the attack surface: a single linked signer address can authorize transactions for multiple subaccounts across different wallets. [7](#0-6) 

---

### Recommendation

Replace the `bytes32` identity comparison with a wallet-address-level check. Extract the first 20 bytes of both `txn.sender` and `txn.liquidatee` and require they differ:

```solidity
require(
    address(uint160(bytes20(txn.sender))) !=
    address(uint160(bytes20(txn.liquidatee))),
    ERR_UNAUTHORIZED
);
```

Additionally, consider whether the linked-signer relationship should be checked: if `getLinkedSigner(txn.sender) == getLinkedSigner(txn.liquidatee)` and both are non-zero, the same key controls both sides and the liquidation should also be rejected.

---

### Proof of Concept

```solidity
// Wallet W controls both subaccounts.
// subaccountA = bytes32(abi.encodePacked(W, bytes12("default")))
// subaccountB = bytes32(abi.encodePacked(W, bytes12("liq")))

// Step 1: deposit collateral into subaccountA, open large perp long.
// Step 2: deposit quote into subaccountB.
// Step 3: wait for perp price to drop below maintenance margin for subaccountA.
// Step 4: W signs:
IEndpoint.LiquidateSubaccount memory liqTx = IEndpoint.LiquidateSubaccount({
    sender:         subaccountB,   // W+"liq"
    liquidatee:     subaccountA,   // W+"default"
    productId:      PERP_PRODUCT_ID,
    isEncodedSpread: false,
    amount:         positionSize,
    nonce:          currentNonce
});
// Signature is valid: recovered address == W == address(uint160(bytes20(subaccountB)))
// On-chain check: subaccountB != subaccountA  → passes
// Result: subaccountB acquires position at liquidation discount;
//         insurance covers subaccountA's quote shortfall.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L395-412)
```text
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```

**File:** core/contracts/ClearinghouseLiq.sol (L447-596)
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

        // it's ok to let initial health become 0
        require(!isAboveInitial(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH);
        require(
            txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
            ERR_SUBACCT_HEALTH
        );

        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;

        emit Liquidation(
            txn.sender,
            txn.liquidatee,
            txn.productId,
            txn.isEncodedSpread,
            txn.amount,
            v.liquidationPayment
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

**File:** core/contracts/interfaces/IEndpoint.sol (L55-62)
```text
    struct LiquidateSubaccount {
        bytes32 sender;
        bytes32 liquidatee;
        uint32 productId;
        bool isEncodedSpread;
        int128 amount;
        uint64 nonce;
    }
```

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L391-412)
```text
        if (txType == IEndpoint.TransactionType.LiquidateSubaccount) {
            IEndpoint.SignedLiquidateSubaccount memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLiquidateSubaccount)
            );
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
                // No liquidation fee for finalization (productId == uint32.max) because:
                // 1) The liquidator receives no profit from finalization
                // 2) Finalization can only occur once per underwater subaccount, eliminating
                //    sybil attack concerns that would otherwise require a fee deterrent.
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
