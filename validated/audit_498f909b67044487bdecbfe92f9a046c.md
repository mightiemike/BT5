### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position `owner` Instead of Caller `sender`, Allowing Any Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller who provides tokens via callback). Because `addLiquidity` accepts an arbitrary `owner` address supplied by the caller, any un-allowlisted actor can bypass the guard by specifying an allowlisted address as `owner`, rendering the allowlist completely ineffective.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` of `addLiquidity` — the entity that actually calls the pool and provides tokens via the swap callback
- `owner` = the caller-supplied parameter — the address that will own the resulting LP position [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

Because `owner` is a free parameter chosen by the caller, any un-allowlisted actor can pass an allowlisted address as `owner`. The extension sees an allowlisted address, passes the check, and the un-allowlisted actor's tokens flow into the pool via the callback while the allowlisted address receives the LP position.

The inconsistency is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper): [4](#0-3) 

---

### Impact Explanation

The deposit allowlist's core invariant — *only allowlisted addresses may provide liquidity to the pool* — is completely broken. A pool admin who deploys this extension to enforce KYC/compliance or restrict liquidity providers to specific market makers achieves no protection: any actor who knows a single allowlisted address can deposit arbitrary amounts. This is an admin-boundary break where an unprivileged path bypasses a pool-admin-configured access control, matching the allowed impact gate.

---

### Likelihood Explanation

Exploitation is trivial and requires no special privileges. The attacker only needs to know one allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads) and call `addLiquidity(owner=allowlisted_address, ...)`. No signature, no timing constraint, no excess allowance required.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to allowlist position owners (not token providers), the parameter name and documentation must be updated to reflect that, and the router layer must enforce that `sender == owner` for direct deposits.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; only `Alice` is allowlisted (`allowedDepositor[pool][Alice] = true`).
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
3. The pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls back to `Bob` for tokens; `Bob` transfers tokens to the pool.
6. Alice receives the LP position; Bob has deposited into a pool that was supposed to reject him.
7. The pool admin's allowlist is completely bypassed with zero privileged access.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
