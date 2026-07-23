### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently drops the `sender` parameter and enforces the allowlist against `owner` (the LP-position recipient) instead. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged address can bypass the guard by supplying an allowlisted address as `owner`.

---

### Finding Description

The `IMetricOmmExtensions.beforeAddLiquidity` interface delivers two distinct addresses:

```
sender  – msg.sender of the addLiquidity call (the entity that provides tokens via callback)
owner   – the address that will hold the minted LP position
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner`, with no requirement that they be equal: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (first positional argument, left unnamed) and enforces the allowlist only against `owner`: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller): [4](#0-3) 

The asymmetry is the root cause. Because `addLiquidity` accepts an arbitrary `owner` with no `msg.sender == owner` guard, any caller can name an allowlisted address as `owner`, pass the extension check, supply tokens through the callback, and have the LP position minted to that allowlisted address — all while the actual depositor (the token provider) is never checked.

---

### Impact Explanation

The deposit allowlist guard is fully bypassed. An unauthorized address can:

1. Deposit tokens into a pool that is supposed to be restricted (e.g., KYC/AML, institutional-only, or whitelist-gated pools).
2. Manipulate the pool's liquidity distribution by depositing large amounts for an allowlisted address, then coordinating with that address to withdraw — effectively laundering unrestricted capital through the pool.
3. Undermine any pool-admin invariant that depends on controlling who provides liquidity (e.g., preventing adversarial LPs from skewing bin balances before a large swap).

The LP position goes to the allowlisted `owner`, but the tokens enter the pool from an unauthorized source, breaking the solvency and access-control invariants the pool admin intended to enforce.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity(allowlisted_address, ...)` directly. The only prerequisite is knowing one allowlisted address, which is publicly readable via `allowedDepositor(pool, addr)`. Likelihood is high.

---

### Recommendation

Check `sender` (the actual token provider) instead of `owner`, mirroring `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

If the intended semantic is to gate who may *hold* LP positions (i.e., restrict `owner`), the allowlist mapping and admin-facing documentation must be updated to reflect that, and a separate `sender` check should be added if token-provider restriction is also desired.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` in the `BEFORE_ADD_LIQUIDITY_ORDER` slot.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted.
3. Unauthorized address `eve` calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner  ← allowlisted, passes the check
       salt,
       deltas,
       callbackData,   // eve implements the callback and transfers tokens
       extensionData
   );
   ```
4. `ExtensionCalling._beforeAddLiquidity` encodes `(sender=eve, owner=alice, ...)` and calls the extension.
5. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints the LP position to `alice`; `eve`'s callback transfers the tokens.
7. `eve` has deposited into the restricted pool. The allowlist guard was never applied to the actual depositor. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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
