### Title
`DepositAllowlistExtension` Checks Caller-Controlled `owner` Instead of `sender`, Allowing Any Unprivileged Actor to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently drops the `sender` parameter (the actual `msg.sender` of `pool.addLiquidity()`) and instead validates the caller-supplied `owner` argument. Because `owner` is a free parameter that any caller can set to any address, the allowlist gate is trivially bypassed: a non-allowlisted actor sets `owner` to any allowlisted address, the check passes, and the actor's tokens enter the pool — violating the admin-configured security boundary.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Inside `ExtensionCalling._beforeAddLiquidity`, both values are ABI-encoded and forwarded verbatim to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but declares the first parameter unnamed, discarding it entirely, and gates only on `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free, caller-controlled parameter — there is no constraint in `addLiquidity` that ties `owner` to `msg.sender`: [4](#0-3) 

By contrast, `removeLiquidity` does enforce `msg.sender == owner`: [5](#0-4) 

```solidity
if (msg.sender != owner) revert NotPositionOwner();
```

This asymmetry is the root cause: the allowlist is checked against a value the attacker controls, while the only binding identity check (`msg.sender == owner`) lives in the *remove* path, not the *add* path.

---

### Impact Explanation

**Admin-boundary break / broken core pool functionality.**

The `DepositAllowlistExtension` is the sole mechanism by which a pool admin restricts who may become an LP (e.g., KYC/AML compliance, whitelist-only pools). Because the guard checks `owner` rather than `sender`:

1. **Allowlist is fully bypassed.** Any unprivileged actor can call `pool.addLiquidity(allowlistedAddress, salt, deltas, …)`. The extension sees `allowedDepositor[pool][allowlistedAddress] == true` and permits the call. The actor's tokens are pulled via the swap-callback mechanism from `msg.sender` (the attacker), deposited into the pool, and the LP shares are credited to `allowlistedAddress`.

2. **Pool state is manipulated by unauthorized actors.** The attacker can add liquidity to arbitrary bins, shifting `curBinIdx`, `curPosInBin`, and `binTotals`, which directly affects the price curve seen by subsequent swappers and the per-share value seen by the `OracleValueStopLossExtension`.

3. **Allowlisted LPs may be force-credited with positions they did not request.** An attacker can deposit into an existing `(allowlistedAddress, salt)` position key, diluting or altering that LP's bin exposure without their consent.

4. **The attacker cannot withdraw** (blocked by `msg.sender != owner` in `removeLiquidity`), so the attack is not directly profitable — but it is a zero-cost pool-state manipulation tool for any actor willing to spend tokens, and it completely nullifies the admin's security boundary.

---

### Likelihood Explanation

**High.** The bypass requires only a single direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address. No special role, flash loan, or oracle manipulation is needed. Any on-chain observer can read `allowedDepositor` mappings (public storage) to find a valid `owner` value. The pool does not validate that `msg.sender == owner` on the add path.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    // Gate on the actual depositor (msg.sender of addLiquidity), not the position owner.
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is "only allowlisted addresses may own positions", then both `sender` and `owner` should be checked. If the intended semantic is "only allowlisted actors may initiate deposits", checking `sender` alone is sufficient.

---

### Proof of Concept

**Setup:**
- Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
- Pool admin calls `setAllowedToDeposit(P, alice, true)` — Alice is the only allowlisted depositor.
- Bob is not allowlisted.

**Attack:**
```solidity
// Bob (not allowlisted) calls addLiquidity with owner = alice
pool.addLiquidity(
    alice,          // owner — allowlisted, passes the guard
    0,              // salt
    deltas,         // bin shares Bob wants to inject
    callbackData,   // Bob's contract pays in the callback
    extensionData
);
// Extension checks: allowedDepositor[pool][alice] == true → no revert
// Bob's tokens are pulled from Bob's contract via the add-liquidity callback
// LP shares are credited to alice at key keccak256(alice, 0)
// Bob has bypassed the allowlist and manipulated pool bin state
```

**Result:** Bob, an unprivileged actor, successfully deposits into a restricted pool. The `DepositAllowlistExtension` guard is completely ineffective against any caller who knows one allowlisted address.

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
