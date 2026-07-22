### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address supplied by the caller. The `DepositAllowlistExtension.beforeAddLiquidity` hook enforces the allowlist against `owner` (the position recipient) rather than `sender` (the actual `msg.sender`). Any unprivileged address can therefore bypass the allowlist entirely by passing any already-allowed address as `owner`, forcing unpermissioned liquidity into a pool that is supposed to be access-controlled.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts `owner` as a caller-supplied parameter with no requirement that `msg.sender == owner`: [1](#0-0) 

The pool then forwards both `msg.sender` (as `sender`) and the caller-supplied `owner` into the `beforeAddLiquidity` hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed) argument but silently ignores it. The allowlist check is performed exclusively against `owner`: [3](#0-2) 

Because `owner` is fully attacker-controlled, any address can call `addLiquidity(allowedAddress, salt, deltas, ...)`. The extension sees `allowedDepositor[pool][allowedAddress] == true` and passes. The token pull happens via the swap-callback on `msg.sender` (the actual, unpermissioned caller), and the LP position is minted to `allowedAddress`.

Contrast this with `removeLiquidity`, which correctly enforces `msg.sender == owner`: [4](#0-3) 

The asymmetry means the deposit gate is entirely absent while the withdrawal gate is sound.

The `SwapAllowlistExtension` does not share this flaw — it checks `sender` (which equals `msg.sender` from the pool's `swap` call) and cannot be spoofed: [5](#0-4) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may provide liquidity to a permissioned pool (KYC pools, private LP pools, regulatory-compliant pools). Bypassing it means:

1. **Allowlist is completely nullified** — any address can inject liquidity into a pool that is supposed to be access-controlled.
2. **Compliance invariant broken** — the pool admin's intent (only approved depositors) is violated without any admin action.
3. **Unwanted positions forced onto allowed addresses** — the LP position is credited to the `owner` (an allowed address) without their consent; they must actively call `removeLiquidity` to undo it.
4. **Pool state contaminated** — bin totals, share accounting, and watermarks in `OracleValueStopLossExtension` are all affected by the injected liquidity.

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity`.
- Requires only knowledge of one allowed address (publicly readable from `allowedDepositor` mapping or event logs).
- The attacker bears the token cost but gains the ability to violate access control at will.
- Likelihood: **High** — trivially reachable by any external actor once a permissioned pool is deployed.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner` (the position recipient):

```solidity
// current (vulnerable)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// fixed
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`, which gates on `sender`.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with DepositAllowlistExtension E.
  - Admin calls E.setAllowedToDeposit(P, alice, true).
  - Bob (not on allowlist) wants to add liquidity.

Attack:
  1. Bob calls P.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)
  2. Pool calls E.beforeAddLiquidity(sender=bob, owner=alice, ...)
  3. Extension checks allowedDepositor[P][alice] → true → passes.
  4. LiquidityLib.addLiquidity credits shares to alice.
  5. Pool calls IMetricOmmSwapCallback(bob).metricOmmAddLiquidityCallback(...) — Bob provides tokens.
  6. Bob has successfully deposited into a pool he is not allowed to access.
     Alice now holds an LP position she never requested.
``` [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
