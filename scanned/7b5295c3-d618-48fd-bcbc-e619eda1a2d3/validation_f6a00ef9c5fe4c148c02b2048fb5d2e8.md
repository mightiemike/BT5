### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, letting any non-allowlisted address bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces its allowlist check against the `owner` parameter (the position beneficiary) rather than the `sender` parameter (the address actually calling `addLiquidity` and paying tokens). Because `owner` is a freely-chosen argument supplied by the caller, any non-allowlisted address can pass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension receives both values but discards `sender` (first unnamed parameter) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The `SwapAllowlistExtension` correctly checks `sender` (the actual swap caller):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The asymmetry is the root cause. `owner` is an arbitrary address the caller supplies; it is not authenticated. `sender` is `msg.sender` and cannot be spoofed.

---

### Impact Explanation

Any non-allowlisted address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension check passes because `allowlistedAddress` is in the allowlist. The non-allowlisted caller pays tokens via the `metricOmmModifyLiquidityCallback` and the position is credited to `allowlistedAddress`. The pool admin's access-control boundary is broken: a permissioned pool that is supposed to accept deposits only from vetted addresses accepts deposits from anyone who knows one allowlisted address. This is an unprivileged path bypassing an admin-configured guard, matching the "Admin-boundary break" impact class. Secondary consequences include unrestricted manipulation of bin balances in pools that also carry `OracleValueStopLossExtension` (watermark inflation) or `PriceVelocityGuardExtension` (state seeding), and circumvention of KYC/compliance requirements the pool admin intended to enforce.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any address can call `addLiquidity` on a pool; the only input needed is one allowlisted address (publicly readable from `allowedDepositor` mapping or observable on-chain). The bypass is deterministic and requires no oracle manipulation, flash loan, or timing dependency.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`sender` is `msg.sender` of the pool call and cannot be forged, so the check correctly gates the actual depositing address.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (non-allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,   // bob's callback transfers tokens from bob
       extensionData
   );
   ```
4. Inside `beforeAddLiquidity`: `sender = bob` (discarded), `owner = alice` (checked). `allowedDepositor[pool][alice] == true` → guard passes.
5. `bob` pays tokens via callback; the position is minted under `(alice, salt)`.
6. `bob` has successfully added liquidity to a pool he is not permitted to touch. The allowlist is fully bypassed.
7. `alice` (or a colluding party) can call `removeLiquidity` to recover the tokens, completing a round-trip that routes non-allowlisted capital through the restricted pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
