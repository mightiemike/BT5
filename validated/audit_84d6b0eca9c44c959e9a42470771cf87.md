### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unauthorized depositor to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual depositor) and checks the `owner` parameter (the LP position recipient, which is a free caller-controlled argument). Any unauthorized party can bypass the allowlist by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`: [1](#0-0) 

`msg.sender` is the actual depositor (the party who will provide tokens via callback); `owner` is the LP position recipient and is a free parameter supplied by the caller: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` forwards both as `(sender, owner, ...)` to the extension: [3](#0-2) 

The extension receives `sender` as the first argument but discards it (unnamed `address`), then checks `owner` against the allowlist: [4](#0-3) 

Because `owner` is freely chosen by the caller in `addLiquidity(address owner, ...)`, any caller can set `owner` to any allowlisted address and the guard passes unconditionally, regardless of who `sender` (the actual depositor) is.

The `SwapAllowlistExtension` does not share this flaw — it correctly checks `sender` (first parameter): [5](#0-4) 

---

### Impact Explanation

1. **Allowlist fully defeated**: The pool admin's access control is bypassed by any external actor with knowledge of one allowlisted address (publicly readable from `allowedDepositor`). Unauthorized liquidity enters the pool.
2. **Unsolicited LP position forced on allowlisted address**: The allowlisted `owner` receives an LP position they did not request. They can call `removeLiquidity` (which enforces `msg.sender == owner`) to recover the deposited tokens — effectively receiving a free transfer from the attacker's deposit.
3. **Stop-loss watermark corruption**: If the pool is also configured with `OracleValueStopLossExtension`, unauthorized deposits alter per-bin token balances, shifting the per-share metrics used to update high watermarks in `afterSwap`, potentially suppressing or prematurely triggering stop-loss guards for legitimate LPs. [6](#0-5) 

---

### Likelihood Explanation

Exploitation requires only: (a) reading `allowedDepositor[pool][addr]` to find one allowlisted address, and (b) calling `pool.addLiquidity(owner=allowlisted_addr, ...)`. No special privileges, flash loans, or oracle manipulation are needed. The allowlist state is public. [7](#0-6) 

---

### Recommendation

Replace the `owner` check with a `sender` check to match the documented intent:

```solidity
// BEFORE (buggy)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// AFTER (fixed)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension configured.
  allowedDepositor[P][alice] = true
  bob is NOT in the allowlist.

Attack:
  1. bob calls P.addLiquidity(owner=alice, salt=S, deltas=D, callbackData, extensionData)
  2. Pool calls _beforeAddLiquidity(sender=bob, owner=alice, ...)
  3. Extension receives (_, alice, ...) — bob is silently dropped.
  4. Check: allowedDepositor[P][alice] == true → no revert.
  5. Pool credits LP position to alice at salt S.
  6. Pool calls bob.metricOmmSwapCallback(...) — bob's tokens are pulled into the pool.
  7. alice now holds the LP position worth bob's deposited tokens.
  8. alice calls P.removeLiquidity(owner=alice, salt=S, ...) and recovers the tokens.

Result:
  - bob's deposit bypasses the allowlist entirely.
  - alice receives an unsolicited LP position (and can drain it).
  - The pool holds unauthorized liquidity, defeating the pool admin's access control.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```
