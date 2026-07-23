### Title
`DepositAllowlistExtension.beforeAddLiquidity` Guards on `owner` Instead of `sender`, Enabling Allowlist Bypass and Blocking Legitimate Router Deposits — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead evaluates the allowlist against `owner` — the user-supplied position-owner address. This is the direct structural analog to the VaderPoolV2 `from`-parameter bug: a user-controlled address is substituted for the real actor in the access-control check, breaking the guard in both directions.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  real caller  user-supplied
``` [1](#0-0) 

Inside `ExtensionCalling._beforeAddLiquidity`, both values are forwarded to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but **names it `address` (unnamed/discarded)** and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the real caller) and discards `recipient`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The asymmetry is unambiguous: the swap guard is correct; the deposit guard is wrong.

---

### Impact Explanation

**Direction 1 — Allowlist bypass (unprivileged deposit into a restricted pool):**

An attacker who is not on the allowlist calls `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, "")` directly. The extension checks `allowedDepositor[pool][allowlisted_address]` → `true` → passes. The attacker's own `metricOmmModifyLiquidityCallback` pays the tokens; the position is credited to `allowlisted_address`. The pool admin's deposit restriction is fully circumvented by any caller who knows one allowlisted address.

**Direction 2 — Legitimate router blocked (broken core liquidity flow):**

`MetricOmmPoolLiquidityAdder` is the canonical periphery router. When a user calls `addLiquidityExactShares(pool, bob, ...)`, the router calls `pool.addLiquidity(bob, ...)` with `msg.sender = router`. The extension checks `allowedDepositor[pool][bob]`. If the pool admin allowlisted the router (not every individual user), the check fails for every user who is not individually listed, making the router's liquidity-addition path permanently unusable for those users. This is a broken core pool functionality impact. [5](#0-4) 

---

### Likelihood Explanation

- **Bypass**: Any external caller can trigger it with zero privilege. The only requirement is knowing one allowlisted address (publicly readable from `allowedDepositor`). No special token approval on the victim is needed; the attacker pays their own tokens.
- **Router breakage**: Occurs automatically whenever the pool admin allowlists the router contract rather than individual users — the natural and documented usage pattern for a periphery router.

---

### Recommendation

Change `beforeAddLiquidity` to accept and check `sender` (the first argument), exactly mirroring `SwapAllowlistExtension`:

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack (bypass):
  bob deploys a callback contract that pays tokens in metricOmmModifyLiquidityCallback
  bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
  → _beforeAddLiquidity(bob, alice, ...) fires
  → extension checks allowedDepositor[pool][alice] == true → passes
  → pool calls bob's callback → bob pays tokens
  → position credited to alice; bob has bypassed the allowlist

Broken router path:
  pool admin sets allowedDepositor[pool][LiquidityAdder] = true
  user calls LiquidityAdder.addLiquidityExactShares(pool, user, ...)
  → LiquidityAdder calls pool.addLiquidity(user, ...)
  → _beforeAddLiquidity(LiquidityAdder, user, ...) fires
  → extension checks allowedDepositor[pool][user] == false → NotAllowedToDeposit()
  → all router-mediated deposits revert despite the router being allowlisted
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
