### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit guard - (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer of `addLiquidity`) and checks `owner` (the LP position recipient) against the allowlist instead. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address that need not equal `msg.sender`, any non-allowlisted address can bypass the deposit guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

- `sender` = `msg.sender` — the address that will be called back to pay tokens
- `owner` = caller-supplied argument — the address that receives the minted LP shares

`DepositAllowlistExtension.beforeAddLiquidity` overrides the base hook but **drops the first parameter entirely** (it is unnamed and unused) and checks only `owner`:

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

The allowlist is keyed `allowedDepositor[pool][owner]`, so the check passes whenever the supplied `owner` is allowlisted — regardless of who `sender` is. [3](#0-2) 

The base class `BaseMetricExtension.beforeAddLiquidity` carries `onlyPool` and reverts with `ExtensionNotImplemented()`. The override in `DepositAllowlistExtension` replaces that function entirely; Solidity does **not** inherit modifiers through `override`, so `onlyPool` is also silently dropped. However, the primary exploitable flaw is the `owner`-vs-`sender` identity mismatch, not the missing modifier (a non-pool caller would still revert because its address has no allowlist entries). [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict who may provide liquidity (e.g., KYC/AML gating, whitelist-only pools). Because the guard checks the LP-share recipient (`owner`) rather than the token payer (`sender`), the restriction is entirely ineffective:

- A non-allowlisted address calls `pool.addLiquidity(owner = <any allowlisted address>, ...)`.
- The hook sees `allowedDepositor[pool][allowlisted_address] = true` and returns success.
- The non-allowlisted address pays tokens via the `IMetricOmmAddLiquidityCallback` and the LP shares are minted to the allowlisted `owner`.
- The pool admin's access-control intent is violated; the non-allowlisted depositor has effectively entered the pool.

If the allowlisted `owner` is a colluding party (or the attacker controls a second allowlisted wallet), the LP shares can be transferred back, completing a full bypass with no residual cost. Even without collusion, the attacker can force LP shares onto any allowlisted address, which constitutes griefing of that address and disruption of the pool's intended depositor set.

**Severity: Medium–High.** The allowlist guard is the sole on-chain enforcement of the pool's depositor policy. Its complete bypass is a broken core access-control invariant with direct fund-flow consequences (unauthorized liquidity enters the pool).

---

### Likelihood Explanation

- Requires no special role or privilege — any EOA or contract can call `pool.addLiquidity`.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor`).
- The `MetricOmmPoolLiquidityAdder` periphery contract also calls `pool.addLiquidity` with a caller-supplied `owner`, widening the reachable surface.
- Exploitable on every pool that deploys `DepositAllowlistExtension` with a non-trivial allowlist.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual payer) instead of `owner`:

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

Additionally, restore the `onlyPool` modifier (or an equivalent `IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)` check) to prevent direct calls from non-pool addresses from silently succeeding or failing in unexpected ways.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with DepositAllowlistExtension as beforeAddLiquidity hook.
2. Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
   → allowedDepositor[pool][alice] = true
   → bob is NOT allowlisted.

Attack
──────
3. bob calls pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData).

Inside the pool:
  _beforeAddLiquidity(msg.sender=bob, owner=alice, ...)
    → extension.beforeAddLiquidity(bob, alice, ...)
      msg.sender = pool
      check: allowedDepositor[pool][alice] == true  ✓  → no revert

  LiquidityLib.addLiquidity(..., owner=alice, ...)
    → IMetricOmmAddLiquidityCallback(bob).metricOmmAddLiquidityCallback(...)
      bob pays token0/token1
    → LP shares minted to alice

Result: bob (non-allowlisted) has deposited into the pool.
        The deposit allowlist guard is completely bypassed.
``` [2](#0-1) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L45-52)
```text
  function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
