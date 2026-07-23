### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
The `DepositAllowlistExtension` is configured per-pool to restrict who may add liquidity. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of the pool call, i.e., the token-providing caller) and instead validates only the `owner` argument (the position recipient). Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any non-allowlisted address can bypass the guard by naming an allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both `sender` and `owner` to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but marks it unnamed (discarded), then checks only `owner`:

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

The allowlist management API names the controlled entity `depositor`, signalling the intent to restrict the token-providing caller:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [4](#0-3) 

The actual token transfer in `addLiquidity` is driven by a callback to `msg.sender` (the caller), not to `owner`. `owner` is only the recipient of the minted LP shares. There is no `msg.sender == owner` requirement in `addLiquidity` (unlike `removeLiquidity`, which enforces it):

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [5](#0-4) 

### Impact Explanation

A non-allowlisted address `Bob` calls `pool.addLiquidity(owner = Alice, ...)` where `Alice` is allowlisted. The guard checks `allowedDepositor[pool][Alice]` — which passes — while `Bob` (the actual token provider via callback) is never checked. Bob successfully injects liquidity into a pool whose admin explicitly restricted deposits to a curated set of addresses. The pool admin's configured access boundary is bypassed by an unprivileged path without any privileged action or malicious setup.

This is an **admin-boundary break**: the pool admin's deposit allowlist is rendered ineffective for any caller who knows at least one allowlisted address, which is trivially discoverable on-chain from past `addLiquidity` transactions.

### Likelihood Explanation

- No special role or privilege is required; any EOA or contract can call `addLiquidity`.
- The allowlisted `owner` address need not cooperate; the attacker simply names them as the position recipient.
- Allowlisted addresses are publicly visible on-chain.
- The bypass is a single direct call to the pool — no multi-step setup needed.

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // check the actual caller
        && !allowedDepositor[msg.sender][owner]) { // optionally also gate the owner
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The minimal fix is to gate on `sender` (the token-providing caller). Whether `owner` should also be gated is a design decision, but `sender` must be checked to enforce the stated intent of restricting who can deposit.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `Alice` is allowlisted, `Bob` is not.
2. `Bob` calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = ...)`.
3. `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)` is called.
4. Extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the swap callback fires against `Bob` (the caller), pulling Bob's tokens into the pool.
6. LP shares are minted to `Alice`.
7. Bob has successfully deposited into a pool he is not allowlisted for. The deposit allowlist is bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-99)
```text
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
