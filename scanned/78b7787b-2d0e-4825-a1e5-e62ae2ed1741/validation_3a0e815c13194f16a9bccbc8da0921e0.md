### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the position `owner` against the allowlist. Because `MetricOmmPool.addLiquidity` lets any caller supply an arbitrary `owner`, an unauthorized address can pass the allowlist check by naming an already-allowed address as `owner`, while the unauthorized address itself pays the tokens via the callback.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Here `msg.sender` is the actual depositor/payer and `owner` is the position beneficiary supplied by the caller. The extension hook signature is `beforeAddLiquidity(address sender, address owner, ...)`.

`DepositAllowlistExtension.beforeAddLiquidity` drops the first parameter (unnamed `address`) and gates on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

The allowlist is keyed and administered by `owner` (position beneficiary), not by `sender` (the address that actually pays tokens). Compare with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The inconsistency is the root cause: the deposit guard checks the wrong identity field.

---

### Impact Explanation

Any address not in the allowlist can call `pool.addLiquidity(owner = allowedAddress, ...)` directly. The extension evaluates `allowedDepositor[pool][allowedAddress]` which is `true`, so the revert is never triggered. The unauthorized caller pays tokens through the modify-liquidity callback, and the position is credited to `allowedAddress`. The pool admin's deposit restriction is completely nullified: every address that knows any single allowed address can deposit freely. This breaks the core access-control invariant the pool admin configured and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

The bypass requires no special privilege. Any EOA or contract can call `MetricOmmPool.addLiquidity` directly (the pool imposes no caller restriction beyond the extension check). Allowed addresses are discoverable on-chain via `AllowedToDepositSet` events or by reading `allowedDepositor`. The attack is therefore trivially reachable by any actor.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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

Also update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to clarify that the gated identity is the `msg.sender` of `addLiquidity`, not the position owner.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` wired to `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowed.
3. Bob (not in allowlist) calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, "")` directly.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's callback pays the required tokens; the position is recorded under `(alice, salt)`.
6. Bob has deposited into a restricted pool without being on the allowlist. [2](#0-1) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
