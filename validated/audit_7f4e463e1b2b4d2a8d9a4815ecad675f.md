### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` who called `addLiquidity`) and instead gates on `owner` (the LP position recipient). Any non-allowlisted actor can bypass the restriction by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` — the account that actually called the pool
- `owner` = the caller-supplied parameter — the account that will own the resulting LP shares [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both faithfully: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then **drops `sender`** (unnamed first argument) and checks only `owner`: [3](#0-2) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` and discards the second argument: [4](#0-3) 

The asymmetry is the root cause. Because `addLiquidity` accepts a caller-controlled `owner` parameter with no binding to `msg.sender`, any non-allowlisted account can pass an allowlisted address as `owner` and the extension approves the call. [5](#0-4) 

---

### Impact Explanation

The pool admin's deposit allowlist — the sole on-chain mechanism for restricting who may add liquidity to a restricted pool — is fully bypassed by any unprivileged caller. The attacker provides tokens via the `addLiquidity` callback; the allowlisted `owner` receives the LP shares. The pool admin's access-control boundary is broken: an unprivileged path circumvents a factory/pool role check, satisfying the "Admin-boundary break" impact gate.

Secondary consequences:
- Unauthorized liquidity dilutes existing LPs' fee share.
- Allowlisted owners receive LP positions they never requested; while they can call `removeLiquidity` (which enforces `msg.sender == owner`), the forced deposit exposes them to pool risk in the interim. [6](#0-5) 

---

### Likelihood Explanation

Exploitation requires no special privilege. Any account can call `pool.addLiquidity(knownAllowlistedAddress, ...)`. Allowlisted addresses are publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The attacker bears the cost of the deposited tokens (which go to the allowlisted owner), making this a low-cost griefing or allowlist-bypass attack. [7](#0-6) 

---

### Recommendation

Mirror the pattern used in `SwapAllowlistExtension`: check `sender` (the actual caller) and discard `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to allowlist position owners rather than callers, the parameter naming and admin documentation must be updated to reflect that, and the `setAllowedToDeposit` / `isAllowedToDeposit` API should be renamed accordingly to avoid operator misconfiguration.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with DepositAllowlistExtension.
2. Admin calls setAllowedToDeposit(pool, alice, true).
   → allowedDepositor[pool][alice] = true
   → bob (attacker) is NOT on the allowlist.

Attack
──────
3. bob calls pool.addLiquidity(
       owner    = alice,   // allowlisted address
       salt     = 0,
       deltas   = <valid bins>,
       callback = <bob's contract that pays tokens>,
       extData  = ""
   );

4. Pool calls _beforeAddLiquidity(sender=bob, owner=alice, ...).
5. Extension evaluates: allowedDepositor[pool][alice] == true → passes.
6. LiquidityLib mints shares keyed to (alice, salt).
7. bob's callback transfers tokens into the pool.

Result
──────
• bob (non-allowlisted) successfully deposited into a restricted pool.
• alice holds LP shares she never requested.
• Pool admin's allowlist invariant is broken.
``` [3](#0-2) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
