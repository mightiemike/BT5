### Title
Pool Admin Can Frontrun Swaps via Uncapped, Immediately-Effective Per-Bin Fee Increase — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolBinAdditionalFees` lets the pool admin set per-bin additional spread fees (`addFeeBuyE6` / `addFeeSellE6`) to any value up to `uint16.max` (65 535 in E6 = **6.5535%**) with no upper-bound validation and no timelock. The change takes effect in the very next swap. A pool admin who is also an LP (or who coordinates with one) can frontrun a pending swap by spiking the per-bin fee, extracting excess value from the trader. This is the direct Metric OMM analog of the Yieldy `setFee()` frontrunning bug.

---

### Finding Description

`setPoolAdminFees` enforces a hard cap:

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
``` [1](#0-0) 

`setPoolBinAdditionalFees` passes the caller-supplied values straight through with **no cap check**:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool's `setBinAdditionalFees` also performs no cap check — it only validates the bin index:

```solidity
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external onlyFactory nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
{
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
``` [3](#0-2) 

During every swap the per-bin additional fee is added directly on top of the oracle-derived base spread fee:

```solidity
uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6);
``` [4](#0-3) 

The same additive pattern appears in all four swap paths (`buyToken0InBinSpecifiedIn`, `buyToken1InBinSpecifiedOut`, `buyToken1InBinSpecifiedIn`, `buyToken0InBinSpecifiedOut`): [5](#0-4) [6](#0-5) [7](#0-6) 

Because there is no timelock and no cap, the pool admin can call `setPoolBinAdditionalFees(pool, curBin, 65535, 65535)` in the same block as (or immediately before) a trader's swap, raising the effective fee for that bin by up to **6.5535%** with zero notice to the trader.

The protocol's own documentation acknowledges that admin fee changes need guardrails — `setPoolAdminFees` has a cap, oracle rotation has a timelock, and `OracleValueStopLossExtension` timelocks drawdown/decay changes so "LPs can react": [8](#0-7) 

Per-bin fees are the only admin-controlled fee knob that has **neither** a cap **nor** a timelock.

---

### Impact Explanation

A trader submitting a swap against bin `k` at an expected fee of, say, 1% (base spread) can be made to pay up to **7.5535%** (1% base + 6.5535% per-bin) in the same block. The excess fee accrues to LPs in that bin. A pool admin who is also an LP — or who coordinates with an LP — directly extracts the difference from the trader's principal. This is a direct, quantifiable loss of user funds on every frontrun swap, satisfying the "direct loss of user principal" impact gate.

---

### Likelihood Explanation

The pool admin role is a single EOA or multisig that can call `setPoolBinAdditionalFees` at any time. On chains with a public mempool (Ethereum mainnet, most L2s), the admin can observe a pending swap and insert the fee-spike transaction ahead of it. Even without mempool visibility, the admin can pre-emptively spike fees on the active bin and restore them after the swap, with no on-chain evidence of intent. The attack requires no special setup beyond holding the pool admin role.

---

### Recommendation

1. **Add a cap** in `setPoolBinAdditionalFees` analogous to the cap in `setPoolAdminFees`:

```solidity
if (addFeeBuyE6 > maxAdminBinFeeE6) revert BinFeeTooHigh();
if (addFeeSellE6 > maxAdminBinFeeE6) revert BinFeeTooHigh();
```

2. **Add a timelock** (propose + execute pattern, identical to `proposePoolPriceProvider` / `executePoolPriceProviderUpdate`) so traders and LPs have advance notice of per-bin fee changes. [9](#0-8) 

---

### Proof of Concept

```
State: pool has bin 0 active, base spreadFeeE6 = 10_000 (1%), addFeeBuyE6 = 0.
Trader signs: swap 1000 USDC → ETH, expects ~1% fee, submits tx to mempool.

Block N:
  tx1 (pool admin): factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)
      → bin 0 addFeeBuyE6 = 65535 (6.5535%), effective immediately
  tx2 (trader):     router.exactInputSingle(...)
      → swap executes; effective buy fee = 1% + 6.5535% = 7.5535%
      → trader receives ~6.5% fewer tokens than quoted

Block N+1:
  tx3 (pool admin): factory.setPoolBinAdditionalFees(pool, 0, 0, 0)
      → fee restored; excess LP balance collected via collectPoolFees
```

The only on-chain check that fires is `bin < LOWEST_BIN || bin > HIGHEST_BIN`; the fee values 65535/65535 pass without revert. [2](#0-1) [10](#0-9)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L473-507)
```text
  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function proposePoolPriceProvider(address pool, address newPriceProvider)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    uint256 timelock = priceProviderTimelock[pool];
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, newPriceProvider);

    address mutableProvider = PoolStateLibrary._slot3(pool);
    address current = mutableProvider != address(0) ? mutableProvider : p.immutablePriceProvider;
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function executePoolPriceProviderUpdate(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    address pending = pendingPriceProvider[pool];
    if (pending == address(0)) revert NoPriceProviderChangeProposed();
    uint256 execAfter = pendingPriceProviderExecuteAfter[pool];
    // forge-lint: disable-next-line(block-timestamp) -- timelock enforcement legitimately relies on `block.timestamp`.
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
    delete pendingPriceProvider[pool];
    delete pendingPriceProviderExecuteAfter[pool];
    emit PoolPriceProviderUpdated(pool, pending);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L540-541)
```text
    uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L999-999)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1088-1088)
```text
            params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1177-1177)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-16)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
```
