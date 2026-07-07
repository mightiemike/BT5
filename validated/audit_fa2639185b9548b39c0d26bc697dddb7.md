Now I have enough context to fully trace the exploit. Let me verify the `updateStates` guard and the `executeSlowModeTransaction` time check in one final pass.

### Title
Slow-Mode Deposit Credited at Pre-Tick Multiplier Captures Full `dt` Interest Without Time-Weighted Exposure — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`executeSlowModeTransaction()` is an unrestricted external function. After the 3-day slow-mode delay, any caller can trigger it to credit a deposit at the **current** `cumulativeDepositsMultiplierX18`. Because `updateStates` (SpotTick) inflates that multiplier retroactively for the entire elapsed `dt` (up to `7 * SECONDS_PER_DAY - 1` seconds), a depositor who is credited one block before a large SpotTick captures the full `dt` worth of interest without having held a credited balance for that period.

---

### Finding Description

**Step 1 — Deposit queued.** The attacker calls `depositCollateralWithReferral`, which transfers tokens immediately and enqueues a slow-mode tx with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY`. [1](#0-0) 

**Step 2 — Balance credited at stale multiplier.** After 3 days, the attacker calls the unrestricted `executeSlowModeTransaction()`. This passes the only guard (`txn.executableAt <= block.timestamp`) and calls `clearinghouse.depositCollateral` → `spotEngine.updateBalance` → `_updateBalanceNormalized`. [2](#0-1) [3](#0-2) [4](#0-3) 

Inside `_updateBalanceNormalized`, for a fresh account (`amountNormalized = 0`):

```
newAmount = 0 * M + balanceDelta = balanceDelta
balance.amountNormalized = balanceDelta / M          // stored at current M
totalDepositsNormalized += balanceDelta / M
``` [5](#0-4) 

**Step 3 — SpotTick inflates the multiplier.** The sequencer submits a SpotTick with `dt` up to `7 * SECONDS_PER_DAY - 1`. `updateStates` calls `_updateState`, which computes `depositRateMultiplierX18` from utilization and borrow rate, then sets:

```
cumulativeDepositsMultiplierX18 = M * depositRateMultiplierX18   // = M'
``` [6](#0-5) [7](#0-6) 

**Step 4 — Attacker withdraws at inflated multiplier.** `balanceNormalizedToBalance` returns:

```
balance = amountNormalized * M' = (balanceDelta / M) * M' = balanceDelta * (M'/M)
```

Interest captured = `balanceDelta * (depositRateMultiplier(dt) - 1)` — the full `dt` period's yield. [8](#0-7) 

**Root cause.** There is no timestamp recorded when a balance is credited, and no pro-rata adjustment when `updateStates` runs. The normalized balance `amountNormalized = balanceDelta / M` is indistinguishable from a balance that has been held since the last SpotTick. When the multiplier is inflated by `dt`, every normalized unit — including the one just deposited — earns the full `dt` interest.

---

### Impact Explanation

An attacker extracts yield from the pool proportional to `deposit * utilization * borrowRate * dt` (up to ~7 days) without bearing borrow risk or providing liquidity for that period. This dilutes the yield of legitimate long-term depositors and can drain the interest pool at scale if repeated.

---

### Likelihood Explanation

- `executeSlowModeTransaction()` has no access control beyond the 3-day time lock, which the attacker satisfies by design.
- SpotTicks are submitted periodically, not every block, so there is always a window between the attacker's credit and the next tick.
- The attacker needs no privileged role, no sequencer cooperation, and no oracle manipulation.
- The only practical constraint is FIFO queue ordering: the attacker's slow-mode tx must be at the head of the queue, or all preceding txs must already be processed.

---

### Recommendation

When `_updateBalanceNormalized` credits a new deposit, record the current `cumulativeDepositsMultiplierX18` as the deposit's "entry multiplier" and only begin accruing interest from that point forward. One concrete approach: when `updateStates` runs, do **not** retroactively apply the new multiplier to balances deposited after the last tick — instead, track a per-balance `lastDepositMultiplierX18` and compute accrued interest as `amountNormalized * (currentMultiplier / lastDepositMultiplier)`. Alternatively, require that slow-mode deposits are always processed by the sequencer (i.e., remove the permissionless `executeSlowModeTransaction()` path or gate it so it can only execute after the next SpotTick has been applied).

---

### Proof of Concept

```solidity
// Hardhat test (chainid 31337 — slow-mode reverts loudly, evm_increaseTime available)

async function test_interestCapture() {
    // 1. Attacker deposits 1000 USDC via slow-mode
    await endpoint.connect(attacker).depositCollateralWithReferral(
        attackerSubaccount, QUOTE_PRODUCT_ID, 1000e6, ""
    );

    // 2. Advance 3 days so slow-mode tx is executable
    await ethers.provider.send("evm_increaseTime", [3 * 86400]);
    await ethers.provider.send("evm_mine", []);

    // 3. Attacker credits balance at current multiplier M (no SpotTick yet)
    await endpoint.connect(attacker).executeSlowModeTransaction();

    const balanceBefore = await spotEngine.getBalance(QUOTE_PRODUCT_ID, attackerSubaccount);

    // 4. Sequencer submits SpotTick with dt = 6 days (high utilization product)
    const dt = 6 * 86400;
    await endpoint.connect(sequencer).submitTransactionsChecked(
        idx, [encodeSpotTick(currentTime + dt)], e, s, bitmask
    );

    // 5. Attacker's balance now reflects 6 days of interest
    const balanceAfter = await spotEngine.getBalance(QUOTE_PRODUCT_ID, attackerSubaccount);

    // Assert: attacker earned interest for 6 days despite 0 seconds of credited exposure
    assert(balanceAfter.amount > balanceBefore.amount,
        "Attacker captured dt interest without time-weighted exposure");

    // Fuzz: repeat for dt in [1, 7*86400-1], assert monotone interest capture
}
```

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

**File:** core/contracts/Endpoint.sol (L196-199)
```text
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

**File:** core/contracts/SpotEngineState.sol (L33-43)
```text
        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);
```

**File:** core/contracts/SpotEngineState.sol (L129-137)
```text
        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);
```

**File:** core/contracts/SpotEngineState.sol (L180-192)
```text
    function balanceNormalizedToBalance(
        State memory state,
        BalanceNormalized memory balance
    ) internal pure returns (Balance memory) {
        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
    }
```

**File:** core/contracts/SpotEngineState.sol (L265-283)
```text
    function updateStates(uint128 dt) external onlyEndpoint {
        State memory quoteState;
        require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
            State memory state = states[productId];
            if (productId == QUOTE_PRODUCT_ID) {
                quoteState = state;
            }
            if (state.totalDepositsNormalized == 0) {
                continue;
            }
            _updateState(productId, state, dt);
            _setState(productId, state);
        }
    }
```
