### Title
Slow-Mode Queue Lacks Prioritization, Enabling Griefing-Induced Liquidation via Queue Flooding — (File: `core/contracts/Endpoint.sol`)

---

### Summary

The Nado slow-mode queue processes all transaction types in strict FIFO order with no prioritization mechanism. Because `depositCollateralWithReferral` seizes a user's ERC20 tokens immediately but only credits the subaccount when the corresponding slow-mode transaction is eventually dequeued, an attacker who pre-floods the queue with cheap transactions can delay a victim's deposit credit long enough to cause the victim's liquidation — a direct financial loss.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` performs two distinct steps that are not atomic:

1. **Immediate token seizure** — `handleDepositTransfer` pulls the user's ERC20 tokens into the Clearinghouse right away.
2. **Deferred subaccount credit** — a `DepositCollateral` slow-mode transaction is appended to the FIFO queue with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). [1](#0-0) 

The subaccount balance is only updated when `processSlowModeTransactionImpl` eventually reaches that entry and calls `clearinghouse.depositCollateral(txn)`. [2](#0-1) 

The queue is strictly FIFO. `_executeSlowModeTransaction` always pops `slowModeTxs[_slowModeConfig.txUpTo]` — there is no mechanism to skip ahead or assign priority to any transaction type. [3](#0-2) 

`submitSlowModeTransactionImpl` charges only `SLOW_MODE_FEE = $1` per entry for non-admin, non-deposit transaction types (e.g., `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee`). [4](#0-3) [5](#0-4) 

An attacker can therefore pre-fill the queue with a large number of valid slow-mode transactions before a victim's deposit lands, pushing the victim's `DepositCollateral` entry arbitrarily far back. The public `executeSlowModeTransaction()` entry point processes exactly one entry per call, so draining the attacker's entries requires the victim (or any helper) to issue one on-chain call per attacker entry — a gas-intensive and time-consuming process. [6](#0-5) 

The sequencer can process slow-mode entries in bulk via the `ExecuteSlowMode` transaction type, but the slow-mode queue is specifically the censorship-resistance escape hatch for users the sequencer is already ignoring. When the sequencer is not servicing a user's slow-mode entries, the only recourse is the public `executeSlowModeTransaction()` path — which the attacker's flood directly obstructs. [7](#0-6) 

---

### Impact Explanation

A victim who is near their maintenance-health threshold and calls `depositCollateralWithReferral` to add collateral and avoid liquidation will have their tokens seized immediately but their subaccount health will remain unchanged until the slow-mode entry is processed. If the queue is flooded, the deposit credit arrives too late, the victim's health stays below the maintenance threshold, and a liquidator (or the protocol's `N_ACCOUNT`) can liquidate the victim's positions. The victim suffers:

- Loss of position value through forced liquidation at a discount.
- Liquidation fees charged against their subaccount.
- Their deposited tokens remain locked in the Clearinghouse, credited only after the queue drains — after the damage is done. [8](#0-7) 

---

### Likelihood Explanation

**Medium.** The preconditions are:

1. The attacker must pre-flood the queue before the victim's deposit. At $1 per entry, flooding with 5,000 entries costs $5,000 — economically rational if the victim holds a large position.
2. The sequencer must not be rapidly draining the slow-mode queue on the victim's behalf. This is the exact scenario slow mode is designed for (sequencer censorship or liveness failure).
3. The victim must be near their maintenance health threshold — a common situation during volatile markets when users rush to add collateral.

All three conditions can co-occur during a market stress event, which is precisely when attackers are most incentivized to act.

---

### Recommendation

1. **Short term:** Separate `DepositCollateral` from the slow-mode queue entirely. Credit the subaccount atomically within `depositCollateralWithReferral` (as the sequencer-path deposit does), removing the deferred-credit window.
2. **Long term:** If slow-mode deposits must remain queued for sequencer-replay reasons, introduce a priority tier for `DepositCollateral` entries (e.g., a separate high-priority sub-queue processed before the general queue), analogous to the "mandatory" dispatch class in Substrate or the priority field in Tendermint v0.35's `CheckTx`.

---

### Proof of Concept

```
1. Attacker calls submitSlowModeTransaction() 8,000 times with valid
   WithdrawCollateral or LinkSigner payloads, paying $1 each ($8,000 total).
   Queue state: txUpTo=0, txCount=8000.

2. Victim (near maintenance health) calls depositCollateralWithReferral()
   to add $50,000 USDC collateral.
   - $50,000 USDC transferred to Clearinghouse immediately.
   - DepositCollateral slow-mode tx appended at index 8000.
   - Victim's subaccount balance: UNCHANGED.
   Queue state: txUpTo=0, txCount=8001.

3. Sequencer is censoring the victim and does not issue ExecuteSlowMode
   for the victim's entry.

4. Public executeSlowModeTransaction() processes one entry per call.
   Victim or helpers must issue 8,000 calls to drain attacker entries
   before reaching index 8000. Each call costs ~50,000 gas on Ink Chain.

5. During the drain window (hours to days depending on gas economics),
   the victim's health remains below maintenance threshold.

6. A liquidator submits LiquidateSubaccount through the sequencer path
   (unaffected by the slow-mode queue). Victim's positions are liquidated
   at a discount; victim loses position value and pays liquidation fees.

7. After liquidation, the victim's DepositCollateral entry is eventually
   processed — crediting $50,000 USDC to an already-liquidated account.
``` [1](#0-0) [9](#0-8) [6](#0-5)

### Citations

**File:** core/contracts/Endpoint.sol (L144-166)
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
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L185-199)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L253-269)
```text
    function processTransaction(bytes calldata transaction) internal virtual {
        TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == TransactionType.ExecuteSlowMode) {
            SlowModeConfig memory _slowModeConfig = slowModeConfig;
            _executeSlowModeTransaction(_slowModeConfig, true);
            slowModeConfig = _slowModeConfig;
        } else {
            _delegatecallEndpointTx(
                abi.encodeWithSelector(
                    EndpointTx.processTransactionImpl.selector,
                    transaction
                )
            );
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L376-384)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/ClearinghouseLiq.sol (L541-596)
```text
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
