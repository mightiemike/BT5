### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is designed to gate `addLiquidity` calls by depositor address. Its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller) and instead validates only the `owner` parameter (the position recipient). Because `addLiquidity` on the pool accepts any `owner` address from any `msg.sender`, any un-allowlisted caller can bypass the guard by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

- `sender` = `msg.sender` — the address that initiated the call and whose tokens are pulled via the modify-liquidity callback.
- `owner` — the address that receives the minted position shares (can be any address the caller chooses). [1](#0-0) 

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both actors to the extension. [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the `sender` argument is unnamed and completely ignored. The allowlist check is performed only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `owner` is caller-supplied and unconstrained by the pool, any un-allowlisted address can pass the guard by setting `owner` to any allowlisted address.

---

### Impact Explanation

The allowlist guard is fully bypassed. An un-allowlisted caller can:

1. **Add liquidity to a restricted pool** — the pool admin's intent to restrict depositors is defeated; any address can inject tokens into the pool.
2. **Force-deposit into an allowlisted address's position** — the position shares are credited to the allowlisted `owner` without their consent, manipulating their LP exposure and the pool's bin balances/cursor state.
3. **Corrupt pool state** — unauthorized deposits shift bin balances and the cursor position, affecting all subsequent swaps and LP claims for legitimate participants.

This is a direct analog to the external report: a configured guard (`availableCap` / deposit allowlist) is bypassed because the enforcement point checks the wrong value (`owner` instead of `sender`), making the guard a no-op for any unprivileged caller who knows one allowlisted address.

---

### Likelihood Explanation

- Trigger requires no special privilege: any EOA or contract can call `pool.addLiquidity(owner=allowlistedAddress, ...)` directly.
- The allowlisted address is often discoverable on-chain via `AllowedToDepositSet` events.
- No economic loss to the attacker is required beyond gas; the attacker's tokens are deposited but the position goes to `owner`, so the attacker can also use this to grief allowlisted LPs.

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual caller whose tokens are pulled) rather than `owner` (the position recipient):

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

Also audit `SwapAllowlistExtension.beforeSwap` for the symmetric issue (checking `recipient` instead of `sender`).

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension; allowAllDepositors = false.
  - Pool admin calls setAllowedToDeposit(pool, Alice, true).
  - Bob is NOT allowlisted.

Attack:
  1. Bob calls pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...).
  2. Pool calls extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...).
  3. Extension checks allowedDepositor[pool][Alice] → true → no revert.
  4. Pool proceeds; callback pulls tokens from Bob.
  5. Alice's position is credited with shares Bob paid for.

Result:
  - Bob (un-allowlisted) successfully added liquidity to a restricted pool.
  - The allowlist guard is completely bypassed.
  - Alice's position is modified without her consent.
``` [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L181-196)
```text
  /// @inheritdoc IMetricOmmPoolActions
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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
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
