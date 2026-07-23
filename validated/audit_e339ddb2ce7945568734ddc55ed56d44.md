### Title
`DepositAllowlistExtension` Guards `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and checks `owner` instead. Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged address can bypass the allowlist entirely by supplying an already-allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` — the actual caller who triggers the callback and provides tokens
- `owner` = caller-supplied parameter — the address that will own the resulting position [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter unnamed) and checks only `owner`: [3](#0-2) 

The NatSpec for the contract states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is `sender` — the address that calls the pool and funds the callback. Checking `owner` instead means the guard is bound to the wrong identity.

Contrast with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`: [4](#0-3) 

`addLiquidity` has no `msg.sender == owner` requirement (unlike `removeLiquidity`, which enforces it): [5](#0-4) 

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who can provide liquidity to a pool. With this bug the guard is completely inoperative:

- Any unprivileged address can call `addLiquidity(owner = allowlistedAddress, ...)`, pass the extension check, fund the callback, and inject liquidity into a pool that was intended to be restricted.
- The pool admin's configured security boundary is silently bypassed by any caller, breaking the admin-boundary invariant.
- Unauthorized liquidity injection can shift bin positions, dilute existing LP shares, and alter the pool's token composition in ways the admin explicitly sought to prevent.

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a publicly observable allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any address can trigger this at any time on any pool that uses `DepositAllowlistExtension`.

### Recommendation

Replace the `owner` check with `sender` in `beforeAddLiquidity`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

1. Pool P is deployed with `DepositAllowlistExtension`; only `Alice` is allowlisted (`allowedDepositor[P][Alice] = true`).
2. `Bob` (not allowlisted) calls `P.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. The extension receives `(sender=Bob, owner=Alice, ...)`. It checks `allowedDepositor[P][Alice]` → `true`. No revert.
4. `LiquidityLib.addLiquidity` records the position under `(Alice, salt)`. Bob's callback funds the deposit.
5. Bob has injected liquidity into a restricted pool without being allowlisted, fully bypassing the configured guard. [3](#0-2) [6](#0-5)

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
