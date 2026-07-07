### Title
Locked NLP Tokens Included in Health Calculations, Overstating Accessible Collateral — (`File: core/contracts/SpotEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The Nado protocol tracks NLP token balances in two separate data structures: the total balance in `balances[NLP_PRODUCT_ID][subaccount]` and a separate lock queue in `nlpLockedBalanceQueues`. When a user mints NLP, the minted tokens are locked for `NLP_LOCK_PERIOD` (4 days) and cannot be burned until the lock expires. However, the health calculation used by the Clearinghouse reads from the total balance — including locked, inaccessible NLP — at full oracle price. This overstates a user's actual accessible collateral, allowing over-borrowing against tokens that cannot be redeemed during the lock window.

---

### Finding Description

When a user mints NLP via `Clearinghouse.mintNlp`, the SpotEngine credits the full `nlpAmount` to `balances[NLP_PRODUCT_ID][txn.sender]` and simultaneously enqueues a lock entry in `nlpLockedBalanceQueues[txn.sender]` with `unlockedAt = getOracleTime() + NLP_LOCK_PERIOD`. [1](#0-0) 

The lock tracking is purely additive — it does not reduce the value stored in `balances[NLP_PRODUCT_ID][subaccount]`. The two paths are independent: [2](#0-1) 

When `Clearinghouse.getHealth` is called (e.g., after `mintNlp` or `withdrawCollateral`), it calls `spotEngine.getHealthContribution`, which reads `balances[NLP_PRODUCT_ID][subaccount]` — the **total** NLP balance including locked tokens — and multiplies it by the current oracle price and risk weight. The lock queue is never consulted during health computation. [3](#0-2) 

By contrast, `burnNlp` correctly enforces the lock by checking `getNlpUnlockedBalance` before allowing redemption: [4](#0-3) 

This creates a direct desynchronization: health accounting uses the total NLP balance (locked + unlocked), but the user can only redeem the unlocked portion.

---

### Impact Explanation

A user can mint NLP tokens (which become locked for 4 days), receive a health contribution from the full locked balance at oracle price, and then borrow USDC or open leveraged positions against that inflated health. During the 4-day lock window:

- The user **cannot** burn locked NLP to repay debt or restore health.
- If the NLP oracle price declines, the user's health deteriorates below zero with no self-rescue path.
- The protocol must liquidate the user. The liquidator receives the locked NLP but must also wait for the lock to expire before burning it, during which the NLP price may fall further.
- This creates a bad-debt window where the protocol's insurance fund absorbs losses that would not exist if locked NLP were excluded from health.

The impact is amplified early in the protocol's life when NLP supply is small and oracle prices are more volatile, directly mirroring the early-phase amplification described in H-02. [5](#0-4) 

---

### Likelihood Explanation

The trigger is reachable by any unprivileged user through the standard `MintNlp` transaction type processed by `EndpointTx.processTransactionImpl`. No special permissions are required. The scenario activates whenever a user mints NLP and subsequently borrows against the locked balance — a normal usage pattern for liquidity providers seeking yield on their collateral. [6](#0-5) 

---

### Recommendation

Health contributions for NLP tokens should use only the **unlocked** balance. The `getHealthContribution` path in `SpotEngineState` should call `getNlpUnlockedBalance(subaccount)` instead of reading `balances[NLP_PRODUCT_ID][subaccount]` directly when computing the NLP health contribution. Alternatively, a separate "locked NLP" product with zero or heavily discounted risk weights could be introduced to represent the inaccessible portion.

```diff
// In SpotEngineState.getHealthContribution (or equivalent):
- int128 nlpBalance = balances[NLP_PRODUCT_ID][subaccount].amount;
+ int128 nlpBalance = getNlpUnlockedBalance(subaccount).amount;
``` [7](#0-6) 

---

### Proof of Concept

**Setup**: `NLP_LOCK_PERIOD` = 4 days. NLP oracle price = $1.00. `longWeightInitial` = 0.9.

1. User mints 10,000 NLP tokens by depositing 10,000 USDC. All 10,000 NLP are locked until `T + 4 days`.
2. Health contribution from NLP = `10,000 × $1.00 × 0.9 = $9,000`.
3. User withdraws 8,500 USDC. Health check passes: `$9,000 − $8,500 = $500 > 0`.
4. At `T + 2 days`, NLP oracle price drops to $0.85.
5. Health = `10,000 × $0.85 × 0.9 − $8,500 = $7,650 − $8,500 = −$850`. User is undercollateralized.
6. User cannot burn locked NLP (`ERR_UNLOCKED_NLP_INSUFFICIENT`). No self-rescue possible.
7. Liquidator liquidates user, receives 10,000 locked NLP. Liquidator must wait until `T + 4 days` to burn.
8. At `T + 4 days`, NLP price = $0.80. Liquidator burns for `10,000 × $0.80 = $8,000`. Shortfall = $500 absorbed by insurance.

The $500 bad debt arises solely because locked NLP was counted as accessible collateral. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/SpotEngine.sol (L129-137)
```text
    function getNlpUnlockedBalance(bytes32 subaccount)
        external
        returns (Balance memory)
    {
        tryUnlockNlpBalance(subaccount);
        Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
            .unlockedBalanceSum;
        return balanceSum;
    }
```

**File:** core/contracts/SpotEngine.sol (L139-174)
```text
    function handleNlpLockedBalance(bytes32 subaccount, int128 amountDelta)
        internal
    {
        _assertInternal();

        // N_ACCOUNT is not limited by lock period
        if (subaccount == N_ACCOUNT) return;

        tryUnlockNlpBalance(subaccount);
        if (amountDelta > 0) {
            NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
                subaccount
            ];
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
        } else if (amountDelta < 0) {
            Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
                .unlockedBalanceSum;
            balanceSum.amount += amountDelta;
            nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
        }
    }
```

**File:** core/contracts/SpotEngine.sol (L207-225)
```text
    function updateBalance(
        uint32 productId,
        bytes32 subaccount,
        int128 amountDelta
    ) external {
        _assertInternal();

        State memory state = states[productId];

        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }

        BalanceNormalized memory balance = balances[productId][subaccount];
        _updateBalanceNormalized(state, balance, amountDelta);

        _setBalanceAndUpdateBitmap(productId, subaccount, balance);
        _setState(productId, state);
    }
```

**File:** core/contracts/Clearinghouse.sol (L71-84)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L496-529)
```text
        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/EndpointTx.sol (L534-553)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
