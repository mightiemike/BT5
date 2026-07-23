Looking at the `DepositAllowlistExtension` and `addLiquidity` flow in detail to trace the exact parameter binding.

### Title
`DepositAllowlistExtension` checks `owner` (position recipient) instead of `sender` (actual depositor), allowing any unprivileged caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives both `sender` (the actual `msg.sender` of `addLiquidity`) and `owner` (the caller-supplied position recipient). The extension silently discards `sender` and only checks `allowedDepositor[pool][owner]`. Because `addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can call `addLiquidity(allowlisted_address, ...)`, pass the allowlist check via the allowlisted `owner`, and deposit tokens into the pool — fully bypassing the access gate the pool admin configured.

---

### Finding Description

**Root cause — wrong parameter checked in the hook:**

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` and forwards them to every configured extension:

```solidity
// ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first argument `_` (discarded) and only checks the second argument `owner`:

```solidity
// DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

**No `msg.sender == owner` guard in the pool:**

`MetricOmmPool.addLiquidity` accepts any caller-supplied `owner` without verifying the caller is that owner:

```solidity
// MetricOmmPool.sol L182-196
function addLiquidity(address owner, uint80 salt, ...) external nonReentrant(...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
``` [3](#0-2) 

`removeLiquidity`, by contrast, **does** enforce `msg.sender == owner`:

```solidity
// MetricOmmPool.sol L206
if (msg.sender != owner) revert NotPositionOwner();
``` [4](#0-3) 

This asymmetry is the crux: anyone can deposit *into* another address's position, but only that address can withdraw.

---

### Impact Explanation

1. **Allowlist fully bypassed**: An unprivileged address `Bob` calls `pool.addLiquidity(alice, ...)` where `alice` is allowlisted. The extension checks `allowedDepositor[pool][alice]` → `true` → passes. Bob's `metricOmmSwapCallback` is invoked; Bob transfers tokens to the pool; Alice's position grows. Bob has deposited into a pool he is not permitted to access.

2. **Existing LP dilution**: Every unauthorized deposit reduces the proportional share of existing allowlisted LPs in the affected bins, directly reducing their claim on accrued spread fees and pool assets.

3. **Griefing / position lock**: Because only `owner` can call `removeLiquidity`, Bob's tokens are permanently locked in Alice's position unless Alice voluntarily removes and returns them. Bob cannot recover the funds.

4. **Access-control invariant broken**: The pool admin's intent — a permissioned LP set — is silently violated. Any on-chain observer can identify allowlisted addresses (via `AllowedToDepositSet` events) and exploit them as proxies.

---

### Likelihood Explanation

- **Trigger**: Unprivileged, requires no special role. Any EOA or contract can call `addLiquidity` directly on the pool.
- **Information needed**: The attacker only needs one allowlisted address, which is publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
- **No economic barrier**: The attacker deposits their own tokens; the cost is only gas plus the deposited amount (which is locked, not lost to a third party).
- **Realistic scenario**: Pools using `DepositAllowlistExtension` for regulatory compliance or curated LP sets are the exact target; the bypass is trivially executable.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check the first parameter (`sender` — the actual `msg.sender` of `addLiquidity`) rather than `owner`:

```solidity
// Fix: check sender, not owner
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Alternatively, enforce `msg.sender == owner` inside `MetricOmmPool.addLiquidity` (or add an optional flag), so that the `owner` parameter cannot be used as a proxy identity.

---

### Proof of Concept

```solidity
// Setup: pool has DepositAllowlistExtension; alice is allowlisted, bob is not.
// allowedDepositor[pool][alice] == true
// allowedDepositor[pool][bob]   == false

// Bob executes:
pool.addLiquidity(
    alice,          // owner — allowlisted, passes the extension check
    salt,
    deltas,         // desired liquidity amounts
    callbackData,   // Bob's callback transfers Bob's tokens to the pool
    extensionData
);

// Result:
// - DepositAllowlistExtension checks allowedDepositor[pool][alice] → true → no revert
// - Bob's metricOmmSwapCallback fires; Bob pays tokens into the pool
// - Alice's position (keyed by alice+salt) is credited with the new shares
// - Bob has deposited into a permissioned pool without being allowlisted
// - Bob's tokens are locked; only alice can call removeLiquidity

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
