Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `addLiquidity` caller who pays tokens) and validates only `owner` (the position recipient) against the per-pool allowlist. Because `MetricOmmPool.addLiquidity` accepts any arbitrary `owner` with no restriction on the caller, any unprivileged address can bypass the allowlist by naming an allowlisted address as `owner`. The deposit guard is fully defeated: non-allowlisted principals can deposit tokens into permissioned pools at will, and the named `owner` receives an unwanted LP position bearing price and impermanent-loss risk.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both identities to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address`), checking only `owner`: [3](#0-2) 

Inside the extension, `msg.sender` is the pool address (the extension is called by the pool via `CallExtension.callExtension`), so the effective check is `allowedDepositor[pool][owner]`. The actual depositor (`sender`) is never validated. By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [4](#0-3) 

The inconsistency is the root cause. `addLiquidity` imposes no restriction on who the caller is relative to `owner`: [5](#0-4) 

**Exploit path:**
1. Pool `P` has `DepositAllowlistExtension` with `allowAllDepositors[P] = false` and `allowedDepositor[P][alice] = true`; Mallory is not allowlisted.
2. Mallory calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(mallory, alice, ...)`.
4. Extension checks `allowedDepositor[P][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` credits shares to `alice`'s position.
6. Pool calls `mallory.metricOmmSwapCallback(...)` — Mallory transfers tokens to the pool.
7. Mallory has deposited into the pool despite not being allowlisted; `allowedDepositor[P][mallory]` remains `false`.

The wrong value is `allowedDepositor[msg.sender][owner]` — the check uses the position recipient's allowlist entry instead of the actual depositor's entry.

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for permissioned pools (institutional, KYC-gated, regulatory-compliance venues). The bypass is complete: any address can deposit any amount into such a pool by naming an allowlisted address as `owner`. The allowlisted victim receives an unwanted LP position and bears LP risk (price exposure, impermanent loss) until she removes it, incurring gas costs she did not consent to. This is a broken admin-configured access control with no direct fund theft but with forced LP exposure on victims and full allowlist bypass for the attacker — Medium severity.

## Likelihood Explanation
Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only cost is the token amount deposited, which the attacker controls and can minimize. The attack is trivially repeatable and requires only knowledge of one allowlisted address (which is publicly readable from `allowedDepositor`).

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension.beforeSwap`:

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

If gating by position recipient (`owner`) is also desired, add an explicit second check for `owner` and document the intent clearly.

## Proof of Concept
**Setup:**
- Deploy pool `P` with `DepositAllowlistExtension`; set `allowAllDepositors[P] = false`.
- Call `setAllowedToDeposit(P, alice, true)`. Mallory is not added.

**Attack (Foundry test):**
```solidity
// Mallory calls addLiquidity naming alice as owner
vm.prank(mallory);
pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
// No revert — allowedDepositor[pool][alice] == true passes the check
// Mallory's tokens enter the pool; alice holds an unwanted LP position
assertFalse(depositAllowlistExtension.isAllowedToDeposit(address(pool), mallory));
// Yet Mallory's deposit succeeded — invariant broken
```

**Verification:** `allowedDepositor[P][mallory]` is `false`, yet Mallory's tokens entered the pool and the allowlist guard did not revert.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
