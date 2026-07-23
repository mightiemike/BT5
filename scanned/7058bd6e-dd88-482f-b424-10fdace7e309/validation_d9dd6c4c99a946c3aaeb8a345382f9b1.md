### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual caller of `addLiquidity`) and instead validates the `owner` argument (the position recipient). Any address that is not on the allowlist can bypass the guard by supplying an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." The pool calls the hook with `(sender, owner, …)` where `sender = msg.sender` of `addLiquidity` (the actual depositor/payer) and `owner` is the position-owner parameter supplied by the caller.

The hook signature drops `sender` entirely:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first `address` parameter (the real depositor) is unnamed and never read. The allowlist lookup is keyed on `owner`, not on the actual caller.

Compare with `SwapAllowlistExtension`, which correctly reads `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

The pool always passes `msg.sender` as `sender` to the hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [3](#0-2) 

And `ExtensionCalling` forwards both arguments faithfully:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
``` [4](#0-3) 

The hook receives the correct `sender` but discards it.

---

### Impact Explanation

**Allowlist bypass (unprivileged path):** Any address `B` that is not on the allowlist can call `pool.addLiquidity(owner = A, …)` where `A` is any allowlisted address. The extension checks `allowedDepositor[pool][A]` → passes. `B` pays the tokens via the modify-liquidity callback; `A` receives the LP position. The pool admin's access-control boundary is broken: an actor the admin explicitly excluded can add liquidity to the pool.

**Allowlisted actor incorrectly blocked:** An allowlisted `sender` who specifies a non-allowlisted `owner` (e.g., to create a position on behalf of a third party) is incorrectly rejected, breaking legitimate use of the allowlist.

Both effects violate the stated invariant ("gates `addLiquidity` by depositor address") and constitute an admin-boundary break under the impact gate.

---

### Likelihood Explanation

The bypass requires no special privilege. Any externally-owned account or contract can call `addLiquidity` directly on the pool (or via `MetricOmmPoolLiquidityAdder`) with an allowlisted address as `owner`. The allowlisted address need not cooperate; its address is public on-chain via `allowedDepositor` events or storage reads. Likelihood is **high**.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

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

If the intent is to gate on both the depositor and the owner, both should be checked explicitly.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; only `Alice` is allowlisted (`allowedDepositor[pool][Alice] = true`).
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `extension.beforeAddLiquidity(Bob /*sender*/, Alice /*owner*/, …)`.
4. The extension ignores `Bob` and checks `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `Bob` pays tokens via the callback; `Alice` receives the LP shares.
6. The deposit allowlist has been bypassed by an unprivileged actor.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```
