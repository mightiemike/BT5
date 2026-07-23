### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and only validates the `owner` (position recipient). Because `MetricOmmPool.addLiquidity` lets any caller supply an arbitrary `owner`, an address that is **not** on the allowlist can deposit tokens into a restricted pool by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct actor addresses:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*position owner*/, ...)
``` [1](#0-0) 

`ExtensionCalling` faithfully forwards both to the extension:

```
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (note the anonymous first parameter) and only checks `owner`:

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

`addLiquidity` imposes **no restriction** on who may call it or what `owner` they name — unlike `removeLiquidity`, which enforces `msg.sender == owner`: [4](#0-3) 

The token transfer is pulled from `msg.sender` (the caller) via the liquidity callback, not from `owner`. Therefore the entity that actually provides tokens is `sender`, but the guard only checks `owner`.

---

### Impact Explanation

Any address that is **not** on the deposit allowlist can call `pool.addLiquidity(allowlistedAddress, ...)`, pass the extension check (because `allowlistedAddress` is approved), supply tokens via the callback, and create a liquidity position credited to the allowlisted address. The deposit allowlist — the sole access-control mechanism for restricting who may inject liquidity — is fully bypassed. This breaks the core pool functionality the extension is designed to enforce and violates the invariant that only approved depositors can add liquidity to a restricted pool.

---

### Likelihood Explanation

Allowlisted addresses are publicly readable on-chain via `allowedDepositor` and `allowAllDepositors`. Any actor can enumerate them and immediately exploit the bypass with no privileged access, no special setup, and no cooperation from the allowlisted address. The only cost to the attacker is the gas and the tokens they deposit (which are credited to the allowlisted owner, not returned to the attacker).

---

### Recommendation

Replace the ignored first parameter with a named `sender` and check it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // ← check actual depositor
        && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The exact policy (check `sender`, `owner`, or both) should match the pool admin's intent. If the goal is to restrict who provides tokens, `sender` is the correct check.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; Alice (`0xAlice`) is allowlisted, Bob (`0xBob`) is not.
2. Bob calls `pool.addLiquidity(0xAlice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(0xBob, 0xAlice, ...)`.
4. Extension evaluates `allowedDepositor[pool][0xAlice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls back to `0xBob` to pull tokens.
6. Bob's tokens enter the pool; Alice receives the LP position.
7. Bob has deposited into a pool he is explicitly barred from — the allowlist is bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
